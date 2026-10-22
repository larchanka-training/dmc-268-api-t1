"""Видеть и подключать репозиторий — по роли у провайдера."""

import pytest

from app.domain.enums import RepositoryRole
from app.domain.repository_access import can_connect, can_see


@pytest.mark.parametrize("role", list(RepositoryRole))
def test_any_access_shows_the_repository(role: RepositoryRole) -> None:
    assert can_see(role) is True


def test_no_access_hides_the_repository() -> None:
    assert can_see(None) is False


@pytest.mark.parametrize(
    ("role", "expected"),
    [
        (RepositoryRole.ADMIN, True),
        (RepositoryRole.MAINTAIN, True),
        (RepositoryRole.WRITE, False),
        (RepositoryRole.TRIAGE, False),
        (RepositoryRole.READ, False),
        (RepositoryRole.CUSTOM, False),
        (None, False),
    ],
)
def test_only_admin_and_maintain_connect(role: RepositoryRole | None, expected: bool) -> None:
    assert can_connect(role) is expected
