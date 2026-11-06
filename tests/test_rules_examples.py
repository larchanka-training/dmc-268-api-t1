"""Шаблоны `examples/rules/` — рабочие `.review/rules.md`: владелец копирует их как есть.

Проверка без модели: каждый шаблон разбирается без отклонённых правил, помещается в лимиты и
каждое его правило задевает типичный файл своего стека — опечатка в паттерне ловится здесь, а
не молчанием бота в чужом репозитории.
"""

from pathlib import Path

import pytest

from app.config import Settings
from app.domain.context_budget import BYTES_PER_TOKEN, json_size
from app.domain.repo_rules import applicable_rules, parse_rules_markdown

ROOT = Path(__file__).resolve().parent.parent
EXAMPLES = ROOT / "examples" / "rules"
GUIDE = ROOT / "docs" / "RULES_GUIDE.md"

# Типичные пути стека: каждое правило шаблона должно задеть хотя бы один.
SAMPLE_PATHS: dict[str, tuple[str, ...]] = {
    "python-fastapi": (
        "app/api/users.py",
        "app/routers/items.py",
        "app/services/users.py",
        "alembic/versions/0001_init.py",
        "tests/test_users.py",
    ),
    "typescript-react": (
        "src/pages/Home.tsx",
        "src/api/client.ts",
        "src/pages/Home.test.tsx",
    ),
    "go": (
        "internal/server/handler.go",
        "internal/server/handler_test.go",
    ),
    "security-baseline": (
        "app/api/users.py",
        "src/api/client.ts",
        "cmd/server/main.go",
        "package.json",
        "Dockerfile",
    ),
}

TEMPLATES = sorted(EXAMPLES.glob("*.md"))


def _field_default(name: str) -> int:
    default = Settings.model_fields[name].default
    assert isinstance(default, int)
    return default


def test_every_template_has_sample_paths() -> None:
    assert {path.stem for path in TEMPLATES} == set(SAMPLE_PATHS)


@pytest.mark.parametrize("template", TEMPLATES, ids=lambda p: p.stem)
def test_template_is_accepted_whole(template: Path) -> None:
    rules, rejected = parse_rules_markdown(template.read_text(encoding="utf-8"))
    assert rejected == ()
    assert rules


@pytest.mark.parametrize("template", TEMPLATES, ids=lambda p: p.stem)
def test_every_rule_of_template_matches_its_stack(template: Path) -> None:
    rules, _ = parse_rules_markdown(template.read_text(encoding="utf-8"))
    matched = {rule.name for rule, _ in applicable_rules(rules, SAMPLE_PATHS[template.stem])}
    assert [rule.name for rule in rules if rule.name not in matched] == []


@pytest.mark.parametrize("template", TEMPLATES, ids=lambda p: p.stem)
def test_template_fits_rules_limits(template: Path) -> None:
    text = template.read_text(encoding="utf-8")
    rules, _ = parse_rules_markdown(text)
    assert len(text.encode("utf-8")) <= _field_default("rules_max_bytes")
    # Все правила шаблона сразу, как их увидит модель, если дифф задел каждое.
    items = [{"name": r.name, "files": ["src/module/file.ext"], "checks": list(r.checks)} for r in rules]
    assert json_size(items) / BYTES_PER_TOKEN <= _field_default("rules_max_tokens") / 2


def test_stack_template_combines_with_security_baseline() -> None:
    baseline, _ = parse_rules_markdown((EXAMPLES / "security-baseline.md").read_text(encoding="utf-8"))
    for template in TEMPLATES:
        if template.stem == "security-baseline":
            continue
        rules, _ = parse_rules_markdown(template.read_text(encoding="utf-8"))
        assert {r.name for r in rules}.isdisjoint(r.name for r in baseline), template.stem


def test_guide_links_every_template() -> None:
    guide = GUIDE.read_text(encoding="utf-8")
    assert [p.name for p in TEMPLATES if f"examples/rules/{p.name}" not in guide] == []
