"""Схлопывание замечаний, которые говорят одно и то же дважды.

База следит за этим тоже, уникальным ограничением, поэтому проскочивший здесь
дубль всё равно не запишется. Смысл делать это сначала в домене в том, что
вызывающий код узнаёт, что именно отброшено, а не ловит ошибку целостности.
"""

from collections.abc import Iterable

from app.domain.entities import Finding

type _Identity = tuple[str, str, int | None, int | None, str]


def _identity(finding: Finding) -> _Identity:
    """Привязка целиком, а не один из двух её номеров строк.

    Заполнен всегда только номер строки той стороны, к которой относится
    привязка, поэтому ключ по одному `new_line` дал бы всем замечаниям
    old-стороны в файле один и тот же ключ и схлопнул бы несвязанные в первое.
    """
    anchor = finding.anchor
    return (
        anchor.file_path,
        str(anchor.side),
        anchor.old_line,
        anchor.new_line,
        str(finding.category),
    )


def deduplicate(findings: Iterable[Finding]) -> tuple[list[Finding], list[Finding]]:
    """Разделить замечания на те, что остаются, и те, что уже покрыты.

    Два замечания сталкиваются, когда указывают на один файл, на ту же сторону
    и координаты диффа и совпадают по категории. Побеждает первое; порядок
    сохраняется.
    """
    seen: set[_Identity] = set()
    kept: list[Finding] = []
    dropped: list[Finding] = []
    for finding in findings:
        key = _identity(finding)
        if key in seen:
            dropped.append(finding)
        else:
            seen.add(key)
            kept.append(finding)
    return kept, dropped
