"""Шаблоны `examples/rules/` — рабочие `.review/rules.md`: владелец копирует их как есть.

Проверка без модели: каждый шаблон разбирается без отклонённых правил, каждое его правило
задевает типичный файл своего стека, тесты не получают правил кода, а шаблон стека вместе с
`security-baseline.md` помещается в долю правил на большом PR — опечатка в паттерне или
разросшийся шаблон ловятся здесь, а не молчанием бота в чужом репозитории.
"""

import datetime as dt
from pathlib import Path

import pytest

from app.application.context_assembly import _rule_items
from app.config import Settings
from app.domain.context_budget import BYTES_PER_TOKEN, Budget
from app.domain.entities import RepoRules
from app.domain.enums import RulesStatus
from app.domain.repo_rules import applicable_rules, parse_rules_markdown
from app.domain.untrusted import sanitize

ROOT = Path(__file__).resolve().parent.parent
EXAMPLES = ROOT / "examples" / "rules"
GUIDE = ROOT / "docs" / "RULES_GUIDE.md"
BASELINE = "security-baseline"

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
    BASELINE: (
        "app/api/users.py",
        "src/api/client.ts",
        "cmd/server/main.go",
        "package.json",
        "Dockerfile",
    ),
}

# Тестовые файлы вне `tests/` получают правило тестов своего стека, а не правила кода.
TEST_FILES: dict[str, tuple[str, str]] = {
    "python-fastapi": ("app/test_users.py", "Python Tests"),
    "typescript-react": ("src/api/client.spec.ts", "Frontend Tests"),
    "go": ("internal/server/handler_test.go", "Go Tests"),
}

# Большой PR: 50 файлов стека. Каждое правило несёт в контекст список всех своих файлов,
# поэтому цена правил растёт с числом файлов, а не только с длиной проверок.
LARGE_PR: dict[str, list[str]] = {
    "python-fastapi": [f"app/services/module_{i:02}.py" for i in range(40)]
    + [f"tests/test_module_{i:02}.py" for i in range(10)],
    "typescript-react": [f"src/features/feature_{i:02}/ui/Component.tsx" for i in range(30)]
    + [f"src/features/feature_{i:02}/model/store.ts" for i in range(10)]
    + [f"src/features/feature_{i:02}/ui/Component.test.tsx" for i in range(10)],
    "go": [f"internal/service/module_{i:02}.go" for i in range(40)]
    + [f"internal/service/module_{i:02}_test.go" for i in range(10)],
}

TEMPLATES = sorted(EXAMPLES.glob("*.md"))
STACKS = sorted(p for p in TEMPLATES if p.stem != BASELINE)


def _field_default(name: str) -> int:
    default = Settings.model_fields[name].default
    assert isinstance(default, int)
    return default


def _rules(template: Path):
    rules, rejected = parse_rules_markdown(template.read_text(encoding="utf-8"))
    return rules, rejected


def test_every_template_has_sample_paths() -> None:
    assert {path.stem for path in TEMPLATES} == set(SAMPLE_PATHS)
    assert {path.stem for path in STACKS} == set(TEST_FILES) == set(LARGE_PR)


@pytest.mark.parametrize("template", TEMPLATES, ids=lambda p: p.stem)
def test_template_is_accepted_whole(template: Path) -> None:
    rules, rejected = _rules(template)
    assert rejected == ()
    assert rules


@pytest.mark.parametrize("template", TEMPLATES, ids=lambda p: p.stem)
def test_every_rule_of_template_matches_its_stack(template: Path) -> None:
    rules, _ = _rules(template)
    matched = {rule.name for rule, _ in applicable_rules(rules, SAMPLE_PATHS[template.stem])}
    assert [rule.name for rule in rules if rule.name not in matched] == []


@pytest.mark.parametrize("template", STACKS, ids=lambda p: p.stem)
def test_a_test_file_outside_tests_gets_only_the_test_rule(template: Path) -> None:
    path, test_rule = TEST_FILES[template.stem]
    rules, _ = _rules(template)
    baseline, _ = _rules(EXAMPLES / f"{BASELINE}.md")
    matched = [rule.name for rule, _ in applicable_rules((*baseline, *rules), [path])]
    assert matched == ["Secrets", test_rule]


@pytest.mark.parametrize("template", TEMPLATES, ids=lambda p: p.stem)
def test_template_fits_the_size_limit(template: Path) -> None:
    assert len(template.read_bytes()) <= _field_default("rules_max_bytes")


@pytest.mark.parametrize("template", STACKS, ids=lambda p: p.stem)
def test_stack_with_baseline_fits_the_rules_share_on_a_large_pr(template: Path) -> None:
    """Так, как сборка контекста считает долю правил: `_rule_items` и `Budget.cost`."""
    baseline, _ = _rules(EXAMPLES / f"{BASELINE}.md")
    rules, _ = _rules(template)
    paths = LARGE_PR[template.stem]
    loaded = RepoRules(
        status=RulesStatus.LOADED, rules=(*baseline, *rules), fetched_at=dt.datetime.now(dt.UTC)
    )
    items = _rule_items(loaded, paths, {p: sanitize(p) for p in paths})
    tokens = sum(Budget.cost(item) for item, _ in items) / BYTES_PER_TOKEN
    assert tokens <= _field_default("rules_max_tokens")


def test_stack_template_combines_with_security_baseline() -> None:
    baseline, _ = _rules(EXAMPLES / f"{BASELINE}.md")
    for template in STACKS:
        rules, _ = _rules(template)
        assert {r.name for r in rules}.isdisjoint(r.name for r in baseline), template.stem


def test_guide_links_every_template() -> None:
    guide = GUIDE.read_text(encoding="utf-8")
    assert [p.name for p in TEMPLATES if f"examples/rules/{p.name}" not in guide] == []
