"""Кто может подключить репозиторий — по `permissions` из ответа GitHub."""

import pytest

from app.domain.entities import RepositoryPermissions
from app.domain.repository_access import can_connect


@pytest.mark.parametrize(
    ("admin", "maintain", "expected"),
    [(True, True, True), (True, False, True), (False, True, True), (False, False, False)],
)
def test_only_admin_or_maintain_connects(admin: bool, maintain: bool, expected: bool) -> None:
    assert can_connect(RepositoryPermissions(admin=admin, maintain=maintain)) is expected
