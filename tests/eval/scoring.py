"""Сопоставление замечаний с разметкой и метрики - чистые функции.

Правило совпадения - из `docs/testing/LLM_EVAL.md` §2: тот же файл, та же
сторона диффа, строка в окне `±LINE_WINDOW` от размеченной. Категория
обязательна для общего дефекта и не проверяется у нарушения проектного
правила: у пункта `rules.md` нет своей категории, и модель вправе назвать его
и `correctness`, и `readability`. Верность ссылки на пункт считается отдельно
(доля верных атрибуций), а не входит в совпадение: так конфигурация без правил
сравнима с конфигурацией с правилами по одному и тому же Recall.

Сопоставление один к одному. Пары перебираются от лучшей: сначала те, где
совпал смысл (ссылка на тот же пункт или та же категория), затем по
расстоянию до строки. Так замечание о соседней строке не уводит размеченное
нарушение у замечания, которое назвало его точно.
"""

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from statistics import mean, pstdev

# Часть контракта метрик (LLM_EVAL §2): смена окна делает прошлые цифры несравнимыми.
LINE_WINDOW = 3


@dataclass(frozen=True, slots=True)
class ExpectedIssue:
    """Размеченное нарушение: либо пункт правила, либо общий дефект с категорией."""

    id: str
    path: str
    line: int
    rule: tuple[str, int] | None = None
    category: str | None = None

    def __post_init__(self) -> None:
        if (self.rule is None) == (self.category is None):
            raise ValueError(f"{self.id}: нужна ровно одна из меток - rule или category")


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
    return expected.rule is not None or reported.category == expected.category


def _same_meaning(expected: ExpectedIssue, reported: Reported) -> bool:
    if expected.rule is not None:
        return reported.rule == expected.rule
    return reported.category == expected.category


def match(
    expected: Sequence[ExpectedIssue], reported: Sequence[Reported]
) -> list[tuple[ExpectedIssue, Reported]]:
    """Пары «размеченное - опубликованное», один к одному."""
    candidates = sorted(
        (
            (not _same_meaning(e, r), abs(r.line - e.line), ei, ri)
            for ei, e in enumerate(expected)
            for ri, r in enumerate(reported)
            if _matches(e, r)
        ),
    )
    used_expected: set[int] = set()
    used_reported: set[int] = set()
    pairs: list[tuple[ExpectedIssue, Reported]] = []
    for _, _, ei, ri in candidates:
        if ei in used_expected or ri in used_reported:
            continue
        used_expected.add(ei)
        used_reported.add(ri)
        pairs.append((expected[ei], reported[ri]))
    return pairs


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
    f1 = (
        2 * precision * recall / (precision + recall)
        if precision is not None and recall is not None and precision + recall
        else None
    )
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
