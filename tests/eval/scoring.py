"""Сопоставление замечаний с разметкой и метрики - чистые функции.

Правило совпадения - из `docs/testing/LLM_EVAL.md` §2: тот же файл, та же
сторона диффа, строка в окне `±LINE_WINDOW` от размеченной. Общий дефект
требует той же категории. У пункта `rules.md` своей категории нет, и модель
вправе назвать нарушение по-разному, поэтому разметка пункта перечисляет
допустимые категории (`categories`): замечание засчитывается, если ссылается
на тот же пункт или его категория среди допустимых. Иначе любое замечание
рядом - о читаемости соседней строки - сошло бы за находку нарушения. Верность
ссылки на пункт считается отдельно (доля верных атрибуций), а не входит в
совпадение: так конфигурация без правил сравнима с конфигурацией с правилами
по одному и тому же Recall.

Сопоставление один к одному и оптимальное: из всех допустимых наборов пар
берётся тот, где больше пар; при равном числе - больше пар, совпавших по смыслу
(ссылка на тот же пункт или та же категория); затем - меньше суммарное
расстояние до строк. Жадный выбор от ближайшей пары занижал TP: находка,
ближайшая к одному нарушению, могла быть единственной парой для соседнего.

Для золотого датасета (`tests/eval/dataset/`) разметка шире: сторона диффа
(`side`, по умолчанию `new` - как у корпуса правил), `must_find` и запрещённые
категории кейса (замечание такой категории - FP при любой позиции, в пары не
идёт). Найденное необязательное нарушение - TP для Precision: замечание верное,
шумом его не назвать. В Recall оно не входит ни числителем, ни знаменателем:
иначе найденные необязательные прятали бы пропуск обязательных. Поэтому TP
считается дважды - все пары (`tp`, для Precision) и пары с обязательными
нарушениями (`tp_required`, для Recall). По умолчанию все нарушения обязательные,
и поведение то же, что у корпуса правил.
"""

from collections.abc import Collection, Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from statistics import mean, pstdev
from uuid import UUID

from app.domain.dedup import deduplicate
from app.domain.entities import DiffAnchor, Finding, ReviewRunStep, RuleRef
from app.domain.enums import DiffSide, FindingCategory, FindingSeverity, ReviewRunStage
from app.domain.llm_errors import LlmOutputError

# Часть контракта метрик (LLM_EVAL §2): смена окна делает прошлые цифры несравнимыми.
LINE_WINDOW = 3

# Перебор в `match` экспоненциален по числу эталонных нарушений кейса (2^n
# состояний): при 16 это уже 65 536 масок на каждое замечание. Кейс крупнее -
# повод разбить его, а не ждать минуты на подсчёт.
MAX_EXPECTED_PER_CASE = 16

_CATEGORIES = frozenset(FindingCategory)
_SIDES = frozenset(DiffSide)


@dataclass(frozen=True, slots=True)
class ExpectedIssue:
    """Размеченное нарушение: либо пункт правила, либо общий дефект с категорией."""

    id: str
    path: str
    line: int
    rule: tuple[str, int] | None = None
    category: str | None = None
    # Категории, под которыми замечание без ссылки на пункт засчитывается за нарушение правила.
    categories: tuple[str, ...] = ()
    # Сторона диффа, к которой относится `line`.
    side: str = "new"
    # Обязательное нарушение: пропуск - FN. Необязательное найдено - TP, пропущено - не в счёт.
    must_find: bool = True

    def __post_init__(self) -> None:
        if (self.rule is None) == (self.category is None):
            raise ValueError(f"{self.id}: нужна ровно одна из меток - rule или category")
        if self.rule is not None and not self.categories:
            raise ValueError(f"{self.id}: у нарушения правила нужны допустимые categories")
        if self.rule is None and self.categories:
            raise ValueError(f"{self.id}: categories - только у нарушения правила")
        unknown = [c for c in (self.category, *self.categories) if c and c not in _CATEGORIES]
        if unknown:
            raise ValueError(f"{self.id}: категорий {unknown} нет в FindingCategory")
        if self.side not in _SIDES:
            raise ValueError(f"{self.id}: стороны {self.side!r} нет в DiffSide")


@dataclass(frozen=True, slots=True)
class Reported:
    """Опубликованное замечание в координатах диффа."""

    path: str
    side: str
    line: int
    category: str
    rule: tuple[str, int] | None = None


@dataclass(frozen=True, slots=True)
class CaseScore:
    # Все пары, включая необязательные нарушения: числитель Precision.
    tp: int
    fp: int
    fn: int
    # Найденные обязательные (`must_find`) нарушения правил и общие - для Recall по видам.
    rule_tp: int
    rule_fn: int
    generic_tp: int
    generic_fn: int
    # Совпавшие нарушения правил, у которых замечание сослалось на тот же пункт.
    attributed: int
    # Идентификаторы размеченных нарушений, которые модель пропустила.
    missed: tuple[str, ...]
    # Пары с обязательными нарушениями: числитель Recall.
    tp_required: int
    # Все совпавшие нарушения правил, и необязательные тоже: знаменатель доли верных ссылок.
    rule_matched: int


@dataclass(frozen=True, slots=True)
class Metrics:
    cases: int
    tp: int
    tp_required: int
    fp: int
    fn: int
    precision: float | None
    recall: float | None
    f1: float | None
    rule_recall: float | None
    generic_recall: float | None
    attribution: float | None


def _matches(expected: ExpectedIssue, reported: Reported) -> bool:
    if (reported.path, reported.side) != (expected.path, expected.side):
        return False
    if abs(reported.line - expected.line) > LINE_WINDOW:
        return False
    if expected.rule is not None:
        return reported.rule == expected.rule or reported.category in expected.categories
    return reported.category == expected.category


def _same_meaning(expected: ExpectedIssue, reported: Reported) -> bool:
    if expected.rule is not None:
        return reported.rule == expected.rule
    return reported.category == expected.category


# Оценка набора пар; сравнивается лексикографически, больше - лучше.
type _Score = tuple[int, int, int]


def match(
    expected: Sequence[ExpectedIssue], reported: Sequence[Reported]
) -> list[tuple[ExpectedIssue, Reported]]:
    """Пары «размеченное - опубликованное», один к одному, оптимальные.

    Максимизируется (число пар, число пар с совпавшим смыслом, минус суммарное
    расстояние до строк). Точный перебор - динамика по маскам занятых
    эталонных нарушений, замечания добавляются по одному. При равной оценке
    остаётся набор, найденный раньше: результат детерминирован порядком входа.
    """
    if len(expected) > MAX_EXPECTED_PER_CASE:
        raise ValueError(
            f"в кейсе {len(expected)} эталонных нарушений, допустимо не больше "
            f"{MAX_EXPECTED_PER_CASE}: перебор пар экспоненциален"
        )
    # Маска занятых эталонных нарушений -> лучшая оценка и её пары (индексы).
    best: dict[int, tuple[_Score, tuple[tuple[int, int], ...]]] = {0: ((0, 0, 0), ())}
    for ri, r in enumerate(reported):
        edges = [
            (ei, int(_same_meaning(e, r)), abs(r.line - e.line))
            for ei, e in enumerate(expected)
            if _matches(e, r)
        ]
        if not edges:
            continue
        step = dict(best)
        for mask, ((count, meaning, distance), pairs) in best.items():
            for ei, same, gap in edges:
                bit = 1 << ei
                if mask & bit:
                    continue
                score = (count + 1, meaning + same, distance - gap)
                current = step.get(mask | bit)
                if current is None or score > current[0]:
                    step[mask | bit] = (score, (*pairs, (ei, ri)))
        best = step
    _, pairs = max(best.values(), key=lambda item: item[0])
    return [(expected[ei], reported[ri]) for ei, ri in sorted(pairs)]


def _allowed_pairs(
    expected: Sequence[ExpectedIssue],
    reported: Sequence[Reported],
    forbidden_categories: Collection[str],
) -> list[tuple[ExpectedIssue, Reported]]:
    """Пары без замечаний запрещённых категорий: такое замечание - FP, а не кандидат в TP."""
    allowed = [r for r in reported if r.category not in forbidden_categories]
    return match(expected, allowed)


def score_case(
    expected: Sequence[ExpectedIssue],
    reported: Sequence[Reported],
    *,
    forbidden_categories: Collection[str] = (),
) -> CaseScore:
    pairs = _allowed_pairs(expected, reported, forbidden_categories)
    found = {e.id for e, _ in pairs}
    # Recall - только по must_find: необязательное нарушение не входит ни в TP Recall, ни в FN.
    required = [e for e in expected if e.must_find]
    rule_issues = [e for e in required if e.rule is not None]
    generic_issues = [e for e in required if e.rule is None]
    return CaseScore(
        tp=len(pairs),
        fp=len(reported) - len(pairs),
        fn=sum(1 for e in required if e.id not in found),
        rule_tp=sum(1 for e in rule_issues if e.id in found),
        rule_fn=sum(1 for e in rule_issues if e.id not in found),
        generic_tp=sum(1 for e in generic_issues if e.id in found),
        generic_fn=sum(1 for e in generic_issues if e.id not in found),
        attributed=sum(1 for e, r in pairs if e.rule is not None and r.rule == e.rule),
        missed=tuple(e.id for e in required if e.id not in found),
        tp_required=sum(1 for e in required if e.id in found),
        rule_matched=sum(1 for e, _ in pairs if e.rule is not None),
    )


@dataclass(frozen=True, slots=True)
class CategoryCounts:
    """TP/FP/FN одной категории `FindingCategory` - для среза метрик по категориям.

    `tp` - все пары (Precision среза), `tp_required` - пары с обязательными
    нарушениями (Recall среза), как в `CaseScore`.
    """

    tp: int = 0
    fp: int = 0
    fn: int = 0
    tp_required: int = 0

    def __add__(self, other: CategoryCounts) -> CategoryCounts:
        return CategoryCounts(
            self.tp + other.tp,
            self.fp + other.fp,
            self.fn + other.fn,
            self.tp_required + other.tp_required,
        )


def category_counts(
    expected: Sequence[ExpectedIssue],
    reported: Sequence[Reported],
    *,
    forbidden_categories: Collection[str] = (),
) -> dict[str, CategoryCounts]:
    """Срез `score_case` по категориям, на тех же парах.

    TP и FN относятся к категории эталонного нарушения (у нарушения пункта
    правила своей категории нет - TP идёт в категорию замечания, пропуск в
    срез не входит), FP - к категории замечания. Ключи - только категории,
    где есть хоть один счётчик.
    """
    pairs = _allowed_pairs(expected, reported, forbidden_categories)
    found = {e.id for e, _ in pairs}
    paired = {id(r) for _, r in pairs}
    counts: dict[str, CategoryCounts] = {}

    def add(category: str, delta: CategoryCounts) -> None:
        counts[category] = counts.get(category, CategoryCounts()) + delta

    for e, r in pairs:
        add(e.category or r.category, CategoryCounts(tp=1, tp_required=int(e.must_find)))
    for e in expected:
        if e.must_find and e.id not in found and e.category is not None:
            add(e.category, CategoryCounts(fn=1))
    for r in reported:
        if id(r) not in paired:
            add(r.category, CategoryCounts(fp=1))
    return counts


# Поля `Finding`, которых дедупликация не читает: в отчёт и метрики они не попадают.
_ID = UUID(int=0)
_AT = datetime(2026, 1, 1, tzinfo=UTC)


def _as_finding(r: Reported) -> Finding:
    side = DiffSide(r.side)
    return Finding(
        id=_ID,
        review_run_id=_ID,
        anchor=DiffAnchor(
            r.path,
            side,
            old_line=r.line if side is DiffSide.OLD else None,
            new_line=r.line if side is DiffSide.NEW else None,
        ),
        category=FindingCategory(r.category),
        severity=FindingSeverity.LOW,
        message="-",
        created_at=_AT,
        updated_at=_AT,
        rule=RuleRef(r.rule[0], r.rule[1], "") if r.rule else None,
    )


def duplicates(reported: Sequence[Reported]) -> int:
    """Сколько замечаний отбросила бы `deduplicate` домена (LLM_EVAL §3, Duplicate rate).

    Вызывается сама `deduplicate`, а не её копия: «одна и та же находка» в
    eval и в проде определяется одинаково - привязка плюс категория или плюс
    пункт правила.
    """
    _, dropped = deduplicate(_as_finding(r) for r in reported)
    return len(dropped)


def _ratio(numerator: int, denominator: int) -> float | None:
    return numerator / denominator if denominator else None


def f1_score(precision: float | None, recall: float | None) -> float | None:
    """Гармоническое среднее P и R; у Precision и Recall разные TP, поэтому не через счётчики.

    Если одна сторона не определена, F1 - другая сторона, а не пропуск: модель,
    которая промолчала везде (P без знаменателя, R = 0), получает F1 = 0, иначе
    такой повтор выпал бы из среднего; замечания без обязательных нарушений в
    наборе (R без знаменателя) судятся по Precision. Нет данных ни у одной -
    значения нет.
    """
    if precision is None or recall is None:
        return recall if precision is None else precision
    return 2 * precision * recall / (precision + recall) if precision + recall else 0.0


def aggregate(scores: Iterable[CaseScore]) -> Metrics:
    """Микроусреднение по кейсам: счётчики складываются, отношения - от сумм."""
    items = list(scores)
    tp = sum(s.tp for s in items)
    tp_required = sum(s.tp_required for s in items)
    fp = sum(s.fp for s in items)
    fn = sum(s.fn for s in items)
    precision = _ratio(tp, tp + fp)
    recall = _ratio(tp_required, tp_required + fn)
    rule_tp = sum(s.rule_tp for s in items)
    generic_tp = sum(s.generic_tp for s in items)
    return Metrics(
        cases=len(items),
        tp=tp,
        tp_required=tp_required,
        fp=fp,
        fn=fn,
        precision=precision,
        recall=recall,
        f1=f1_score(precision, recall),
        rule_recall=_ratio(rule_tp, rule_tp + sum(s.rule_fn for s in items)),
        generic_recall=_ratio(generic_tp, generic_tp + sum(s.generic_fn for s in items)),
        attribution=_ratio(sum(s.attributed for s in items), sum(s.rule_matched for s in items)),
    )


def spread(values: Iterable[float | None]) -> tuple[float, float] | None:
    """Среднее и стандартное отклонение по повторам; повтор без значения не считается."""
    present = [v for v in values if v is not None]
    if not present:
        return None
    return mean(present), pstdev(present)


# Этапы, на которых прогон вызывает модель: ревью и верификатор.
_MODEL_STAGES = (ReviewRunStage.ANALYSING, ReviewRunStage.VERIFYING)


@dataclass(frozen=True, slots=True)
class OutputCounts:
    """Вызовы модели по исходу формата ответа (LLM_EVAL §3, Valid-output rate).

    Вызов - этап `analysing` или `verifying` одного прогона; каждая попытка
    внутри него - отдельный шаг (`review_run_steps`), не прошедшая схему - с
    кодом `LLM_INVALID_OUTPUT`. `no_answer` - вызов кончился сбоем без ответа
    модели (таймаут, лимит, сбой отсева) и без более раннего невалидного
    ответа: формат тут не измерить.
    """

    first_try: int = 0
    after_retry: int = 0
    invalid: int = 0
    no_answer: int = 0

    def __add__(self, other: OutputCounts) -> OutputCounts:
        return OutputCounts(
            self.first_try + other.first_try,
            self.after_retry + other.after_retry,
            self.invalid + other.invalid,
            self.no_answer + other.no_answer,
        )

    @property
    def valid_rate(self) -> float | None:
        """Доля ответов, прошедших схему с первой попытки: повтор её не спасает."""
        return _ratio(self.first_try, self.first_try + self.after_retry + self.invalid)


def classify_calls(steps: Sequence[ReviewRunStep]) -> OutputCounts:
    """Исход каждого вызова модели прогона по его шагам; шаги - в порядке начала.

    Этап без вызова модели (верификация без кандидатов, `PROMPT_TOO_LARGE`) не
    считается. Исход вызова - код последнего шага этапа; сбой повтора после
    `LLM_INVALID_OUTPUT` - тоже невалиден: первый ответ схему уже не прошёл. Ответ,
    отвергнутый по смыслу (верификатор не покрыл кандидатов) или обрезанный
    пределом длины, тоже пишется `LLM_INVALID_OUTPUT` и считается невалидным.
    """
    counts = OutputCounts()
    for stage in _MODEL_STAGES:
        attempts = [s for s in steps if s.stage is stage]
        if not any(s.tokens_used is not None for s in attempts):
            continue
        last = attempts[-1].error_code
        retried = any(s.error_code == LlmOutputError.code for s in attempts[:-1])
        if last is None:
            counts += OutputCounts(after_retry=1) if retried else OutputCounts(first_try=1)
        elif last == LlmOutputError.code or retried:
            counts += OutputCounts(invalid=1)
        else:
            counts += OutputCounts(no_answer=1)
    return counts
