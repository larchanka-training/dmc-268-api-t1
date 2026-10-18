"""Корпус eval правил: кейсы PR с разметкой и конфигурации `.review/rules.md`.

Кейс - YAML в `corpus/`: файлы PR (новые файлы, из них стенд строит дифф) и
ожидаемые нарушения. Строка нарушения задаётся подстрокой кода (`contains`), а
не номером: кейс не ломается при правке файла, а в коде не нужна подсказка
для модели. Подстрока обязана встречаться в файле ровно на одной строке.

Конфигурации - что лежит в `.review/rules.md` целевой ветки: `absent` - файла
нет, `rules` - корректные правила, `empty` - пустой файл, `adversarial` - те же
правила плюс инструкции для модели в прозе, в тексте пункта и отдельным
правилом.
"""

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from ..scoring import ExpectedIssue

ROOT = Path(__file__).parent
CORPUS_DIR = ROOT / "corpus"
CONFIGS_DIR = ROOT / "configs"

# `None` - файла правил в целевой ветке нет.
CONFIGS: dict[str, str | None] = {
    "absent": None,
    "rules": (CONFIGS_DIR / "valid.md").read_text(encoding="utf-8"),
    "empty": "",
    "adversarial": (CONFIGS_DIR / "adversarial.md").read_text(encoding="utf-8"),
}


@dataclass(frozen=True, slots=True)
class Case:
    id: str
    title: str
    pr_title: str
    pr_description: str
    files: dict[str, str]
    expected: tuple[ExpectedIssue, ...]


def _line_of(files: dict[str, str], path: str, needle: str, case_id: str) -> int:
    if path not in files:
        raise ValueError(f"{case_id}: файла {path} нет среди файлов PR")
    lines = [n for n, line in enumerate(files[path].splitlines(), start=1) if needle in line]
    if len(lines) != 1:
        raise ValueError(
            f"{case_id}: «{needle}» встречается в {path} на строках {lines}, нужна одна"
        )
    return lines[0]


def _expected(files: dict[str, str], raw: dict[str, Any], case_id: str) -> ExpectedIssue:
    rule = raw.get("rule")
    return ExpectedIssue(
        id=f"{case_id}/{raw['id']}",
        path=raw["path"],
        line=_line_of(files, raw["path"], raw["contains"], case_id),
        rule=(str(rule[0]), int(rule[1])) if rule else None,
        category=raw.get("category"),
    )


def load_case(path: Path) -> Case:
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    files = {str(p): str(content) for p, content in raw["files"].items()}
    return Case(
        id=path.stem,
        title=raw["title"],
        pr_title=raw["pull_request"]["title"],
        pr_description=raw["pull_request"]["description"],
        files=files,
        expected=tuple(_expected(files, item, path.stem) for item in raw["expected"]),
    )


def load_corpus() -> tuple[Case, ...]:
    return tuple(load_case(path) for path in sorted(CORPUS_DIR.glob("*.yaml")))
