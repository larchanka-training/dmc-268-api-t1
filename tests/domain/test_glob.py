"""Фильтр веток репозитория (#313)."""

import pytest

from app.domain.glob import branch_selected


@pytest.mark.parametrize(
    ("branch", "patterns", "selected"),
    [
        ("main", (), True),
        ("main", ("main",), True),
        ("main", ("release/*",), False),
        ("release/1.2", ("release/*",), True),
        ("release/1.2/hotfix", ("release/*",), False),
        ("release/1.2/hotfix", ("release/**",), True),
        ("Main", ("main",), False),
        ("develop", ("main", "dev*"), True),
    ],
)
def test_branch_selected(branch: str, patterns: tuple[str, ...], selected: bool) -> None:
    assert branch_selected(branch, patterns) is selected
