"""Схлопывание замечаний, которые говорят одно и то же дважды.

База следит за этим тоже, уникальным ограничением, поэтому проскочивший здесь
дубль всё равно не запишется. Смысл делать это сначала в домене в том, что
вызывающий код узнаёт, что именно отброшено, а не ловит ошибку целостности.

Замечание по пункту правила сталкивается ещё и по самому пункту: у пункта
нет своей категории, и модель называет одно нарушение то `correctness`, то
`readability` (change `dedup-findings-by-rule-check`).
"""

from collections.abc import Iterable

from app.domain.entities import Finding

type _Anchor = tuple[str, str, int | None, int | None]
type _Identity = tuple[_Anchor, str] | tuple[_Anchor, str, int]


def _anchor(finding: Finding) -> _Anchor:
    """Привязка целиком, а не один из двух её номеров строк.

    Заполнен всегда только номер строки той стороны, к которой относится
    привязка, поэтому ключ по одному `new_line` дал бы всем замечаниям
    old-стороны в файле один и тот же ключ и схлопнул бы несвязанные в первое.
    """
    anchor = finding.anchor
    return (anchor.file_path, str(anchor.side), anchor.old_line, anchor.new_line)


def _identities(finding: Finding) -> tuple[_Identity, ...]:
    """Ключи, по любому из которых замечание совпадает с уже принятым.

    Ключ по категории - тот же, что у уникального индекса замечаний в базе.
    Ключ по пункту правила - только у замечаний со ссылкой на правило.
    """
    by_category: _Identity = (_anchor(finding), str(finding.category))
    if finding.rule is None:
        return (by_category,)
    return by_category, (_anchor(finding), finding.rule.name, finding.rule.check)


def deduplicate(findings: Iterable[Finding]) -> tuple[list[Finding], list[Finding]]:
    """Разделить замечания на те, что остаются, и те, что уже покрыты.

    Два замечания сталкиваются, когда указывают на один файл, на ту же сторону
    и координаты диффа и совпадают по категории или ссылаются на один и тот же
    пункт одного правила. Побеждает первое; порядок сохраняется.
    """
    seen: set[_Identity] = set()
    kept: list[Finding] = []
    dropped: list[Finding] = []
    for finding in findings:
        keys = _identities(finding)
        if seen.intersection(keys):
            dropped.append(finding)
        else:
            seen.update(keys)
            kept.append(finding)
    return kept, dropped
