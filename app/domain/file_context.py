"""Контекст модели по файлам - из тела пакета `context-payload/v1`.

Пакет собран для модели: hunk'и, AST-окружение и правила лежат общими
списками, тексты - в блоках недоверенных данных. Песочница правил и инспектор
замечания показывают его человеку по файлу: что модель видела о файле и что
о нём в пакет не вошло. Тексты - без разметки блоков, как их прочла модель;
пути - как в пакете, после `sanitize`.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from app.domain.untrusted import unfence

# Записи `omitted` о файле, а не о правилах и не о контексте PR.
_FILE_PARTS = frozenset({"file", "hunk", "scopes", "imports", "ast"})


@dataclass(frozen=True, slots=True)
class ContextRule:
    """Правило владельца, показанное модели для файла: имя и пункты по порядку."""

    name: str
    checks: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ContextOmission:
    """Что из контекста файла не досталось модели, и почему."""

    part: str
    reason: str


@dataclass(frozen=True, slots=True)
class FileContext:
    """Что модель видела по одному файлу; `scopes` и `imports` - `None`, если их не передавали."""

    file_path: str
    hunks: tuple[str, ...]
    scopes: str | None
    imports: str | None
    rules: tuple[ContextRule, ...]
    omitted: tuple[ContextOmission, ...]


def file_contexts(body: Mapping[str, Any]) -> tuple[FileContext, ...]:
    """Контекст каждого файла пакета.

    Сначала файлы диффа в его порядке, затем файлы, о которых в пакете есть
    только записи `omitted`: отфильтрованные и не вошедшие целиком.
    """
    omitted: dict[str, list[ContextOmission]] = {}
    for entry in body["omitted"]:
        if entry.get("part") in _FILE_PARTS and "path" in entry:
            omitted.setdefault(entry["path"], []).append(ContextOmission(entry["part"], entry["reason"]))
    paths = dict.fromkeys([item["path"] for item in body["diff"]] + list(omitted))
    return tuple(_file_context(body, path, omitted.get(path, ())) for path in paths)


def _file_context(body: Mapping[str, Any], path: str, omitted: Sequence[ContextOmission]) -> FileContext:
    ast = [item for item in body["ast"] if item["path"] == path]
    return FileContext(
        file_path=path,
        hunks=tuple(unfence(item["hunk"]) for item in body["diff"] if item["path"] == path),
        scopes=next((unfence(item["scopes"]) for item in ast if "scopes" in item), None),
        imports=next((unfence(item["imports"]) for item in ast if "imports" in item), None),
        rules=tuple(
            ContextRule(item["name"], tuple(unfence(check) for check in item["checks"]))
            for item in body["rules"]["items"]
            if path in item["files"]
        ),
        omitted=tuple(omitted),
    )
