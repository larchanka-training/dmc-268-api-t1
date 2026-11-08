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
"""

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from statistics import mean, pstdev

from app.domain.entities import ReviewRunStep
from app.domain.enums import FindingCategory, ReviewRunStage
from app.domain.llm_errors import LlmOutputError

# Часть контракта метрик (LLM_EVAL §2): смена окна делает прошлые цифры несравнимыми.
LINE_WINDOW = 3

# Перебор в `match` экспоненциален по числу эталонных нарушений кейса (2^n
# состояний): при 16 это уже 65 536 масок на каждое замечание. Кейс крупнее -
# повод разбить его, а не ждать минуты на подсчёт.
MAX_EXPECTED_PER_CASE = 16

_CATEGORIES = frozenset(FindingCategory)


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
    tp: int
    fp: int
    fn: int
    rule_tp: int
    rule_fn: int
    generic_tp: int
    generic_fn: int
    # Совпавшие нарушения правил, у которых замечание сослалось на тот же пункт.
    attributed: int
    # Идентификаторы размеченных нарушений, которые модель пропустила.
    missed: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class Metrics:
    cases: int
    tp: int
    fp: int
    fn: int
    precision: float | None
    recall: float | None
    f1: float | None
    rule_recall: float | None
    generic_recall: float | None
    attribution: float | None


def _matches(expected: ExpectedIssue, reported: Reported) -> bool:
    if (reported.path, reported.side) != (expected.path, "new"):
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


def score_case(expected: Sequence[ExpectedIssue], reported: Sequence[Reported]) -> CaseScore:
    pairs = match(expected, reported)
    found = {e.id for e, _ in pairs}
    rule_issues = [e for e in expected if e.rule is not None]
    generic_issues = [e for e in expected if e.rule is None]
    rule_tp = sum(1 for e in rule_issues if e.id in found)
    generic_tp = sum(1 for e in generic_issues if e.id in found)
    return CaseScore(
        tp=len(pairs),
        fp=len(reported) - len(pairs),
        fn=len(expected) - len(pairs),
        rule_tp=rule_tp,
        rule_fn=len(rule_issues) - rule_tp,
        generic_tp=generic_tp,
        generic_fn=len(generic_issues) - generic_tp,
        attributed=sum(1 for e, r in pairs if e.rule is not None and r.rule == e.rule),
        missed=tuple(e.id for e in expected if e.id not in found),
    )


def _ratio(numerator: int, denominator: int) -> float | None:
    return numerator / denominator if denominator else None


def aggregate(scores: Iterable[CaseScore]) -> Metrics:
    """Микроусреднение по кейсам: счётчики складываются, отношения - от сумм."""
    items = list(scores)
    tp = sum(s.tp for s in items)
    fp = sum(s.fp for s in items)
    fn = sum(s.fn for s in items)
    precision = _ratio(tp, tp + fp)
    recall = _ratio(tp, tp + fn)
    # Через счётчики, а не через P и R: при TP = 0 F1 - ноль, а не пропуск,
    # иначе повтор, где модель ничего не нашла, выпал бы из среднего.
    f1 = _ratio(2 * tp, 2 * tp + fp + fn)
    rule_tp = sum(s.rule_tp for s in items)
    generic_tp = sum(s.generic_tp for s in items)
    return Metrics(
        cases=len(items),
        tp=tp,
        fp=fp,
        fn=fn,
        precision=precision,
        recall=recall,
        f1=f1,
        rule_recall=_ratio(rule_tp, rule_tp + sum(s.rule_fn for s in items)),
        generic_recall=_ratio(generic_tp, generic_tp + sum(s.generic_fn for s in items)),
        attribution=_ratio(sum(s.attributed for s in items), rule_tp),
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
