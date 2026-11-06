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
        "config/settings.yaml",
        ".env.example",
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

# До скольких файлов PR шаблон стека вместе с `security-baseline.md` помещается в долю правил.
# Каждое правило несёт в контекст список всех своих файлов, поэтому цена правил растёт с числом
# файлов, а не только с длиной проверок. Те же числа — в `docs/RULES_GUIDE.md`, «Лимиты»: пакет
# контекста вмещает и 80–150 файлов с мелкими ханками, так что порог ниже этого надо знать.
FITS_UP_TO: dict[str, int] = {"python-fastapi": 170, "go": 130, "typescript-react": 90}


def _large_pr(stack: str, files: int) -> list[str]:
    """PR стека: пятая часть — тесты, остальное — код."""
    tests = files // 5
    code = files - tests
    if stack == "python-fastapi":
        return [f"app/services/module_{i:03}.py" for i in range(code)] + [
            f"tests/test_module_{i:03}.py" for i in range(tests)
        ]
    if stack == "typescript-react":
        return (
            [f"src/features/feature_{i:03}/ui/Component.tsx" for i in range(code - code // 4)]
            + [f"src/features/feature_{i:03}/model/store.ts" for i in range(code // 4)]
            + [f"src/features/feature_{i:03}/ui/Component.test.tsx" for i in range(tests)]
        )
    return [f"internal/service/module_{i:03}.go" for i in range(code)] + [
        f"internal/service/module_{i:03}_test.go" for i in range(tests)
    ]

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
    assert {path.stem for path in STACKS} == set(TEST_FILES) == set(FITS_UP_TO)


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
    assert matched == [test_rule]


@pytest.mark.parametrize("template", TEMPLATES, ids=lambda p: p.stem)
def test_template_fits_the_size_limit(template: Path) -> None:
    assert len(template.read_bytes()) <= _field_default("rules_max_bytes")


def _rule_tokens(rules, paths: list[str]) -> float:
    """Так, как сборка контекста считает долю правил: `_rule_items` и `Budget.cost`."""
    loaded = RepoRules(status=RulesStatus.LOADED, rules=tuple(rules), fetched_at=dt.datetime.now(dt.UTC))
    items = _rule_items(loaded, paths, {p: sanitize(p) for p in paths})
    return sum(Budget.cost(item) for item, _ in items) / BYTES_PER_TOKEN


@pytest.mark.parametrize("template", STACKS, ids=lambda p: p.stem)
def test_stack_with_baseline_fits_the_rules_share_up_to_the_documented_size(template: Path) -> None:
    baseline, _ = _rules(EXAMPLES / f"{BASELINE}.md")
    rules, _ = _rules(template)
    files = FITS_UP_TO[template.stem]
    assert _rule_tokens((*baseline, *rules), _large_pr(template.stem, files)) <= _field_default("rules_max_tokens")
    # Гайд называет тот же порог: владелец PR такого размера знает, что правило стека выпадет.
    guide = GUIDE.read_text(encoding="utf-8")
    assert f"| `{template.name}` + `security-baseline.md` | до {files} |" in guide


def test_stack_template_combines_with_security_baseline() -> None:
    baseline, _ = _rules(EXAMPLES / f"{BASELINE}.md")
    for template in STACKS:
        rules, _ = _rules(template)
        assert {r.name for r in rules}.isdisjoint(r.name for r in baseline), template.stem


def test_guide_links_every_template() -> None:
    guide = GUIDE.read_text(encoding="utf-8")
    assert [p.name for p in TEMPLATES if f"examples/rules/{p.name}" not in guide] == []
