"""Корпус eval правил: кейсы PR с разметкой и конфигурации `.review/rules.md`.

Кейс - YAML в `corpus/`: файлы PR (новые файлы, из них стенд строит дифф) и
ожидаемые нарушения. Строка нарушения задаётся подстрокой кода (`contains`), а
не номером: кейс не ломается при правке файла, а в коде не нужна подсказка
для модели. Подстрока обязана встречаться в файле ровно на одной строке.

Нарушение пункта правила (`rule`) обязано перечислить `categories` -
категории, под которыми честное ревью назвало бы это нарушение, не сославшись
на пункт. Замечание без верной ссылки засчитывается, только если его категория
среди них. Общий дефект задаёт одну `category` и `categories` не имеет.

Конфигурации - что лежит в `.review/rules.md` целевой ветки: `absent` - файла
нет, `rules` - корректные правила, `empty` - пустой файл, `adversarial` - те же
правила плюс инструкции для модели в прозе, в тексте пункта и отдельным
правилом.
"""

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from ..scoring import MAX_EXPECTED_PER_CASE, ExpectedIssue

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
        categories=tuple(str(c) for c in raw.get("categories") or ()),
    )


def case_from_raw(
    raw: dict[str, Any], case_id: str, *, expected_required: bool = True
) -> Case:
    """Кейс из разобранного YAML. В корпусе `expected` обязателен: потерянный ключ
    иначе молча сделал бы кейс «чистым PR»; кейсу песочницы (`--case-file`) - нет."""
    if expected_required and "expected" not in raw:
        raise KeyError(f"{case_id}: нет раздела expected (чистый PR - expected: [])")
    files = {str(p): str(content) for p, content in raw["files"].items()}
    expected = tuple(_expected(files, item, case_id) for item in raw.get("expected") or [])
    # Сопоставление перебирает пары экспоненциально: перебор ловим здесь, до
    # платного вызова модели, а не в match() после него.
    if len(expected) > MAX_EXPECTED_PER_CASE:
        raise ValueError(
            f"{case_id}: {len(expected)} эталонных нарушений, допустимо не больше "
            f"{MAX_EXPECTED_PER_CASE}"
        )
    return Case(
        id=case_id,
        title=raw["title"],
        pr_title=raw["pull_request"]["title"],
        pr_description=raw["pull_request"]["description"],
        files=files,
        expected=expected,
    )


def load_case(path: Path, *, expected_required: bool = True) -> Case:
    """Кейс из файла YAML; id - имя файла без расширения."""
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    return case_from_raw(raw, path.stem, expected_required=expected_required)


def load_corpus() -> tuple[Case, ...]:
    return tuple(load_case(path) for path in sorted(CORPUS_DIR.glob("*.yaml")))
