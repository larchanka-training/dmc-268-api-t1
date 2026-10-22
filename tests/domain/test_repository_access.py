"""Кто может подключить репозиторий — по `permissions` из ответа GitHub."""

import pytest

from app.domain.entities import RepositoryPermissions
from app.domain.repository_access import can_connect, has_explicit_access


def _permissions(*granted: str) -> RepositoryPermissions:
    return RepositoryPermissions(**{name: name in granted for name in ("admin", "maintain", "push", "triage")})


@pytest.mark.parametrize(
    ("granted", "expected"),
    [(("admin", "maintain"), True), (("admin",), True), (("maintain",), True), (("push", "triage"), False), ((), False)],
)
def test_only_admin_or_maintain_connects(granted: tuple[str, ...], expected: bool) -> None:
    assert can_connect(_permissions(*granted)) is expected


@pytest.mark.parametrize(
    ("granted", "expected"),
    [(("admin",), True), (("maintain",), True), (("push",), True), (("triage",), True), ((), False)],
)
def test_only_pull_is_not_explicit_access(granted: tuple[str, ...], expected: bool) -> None:
    """Один `pull` GitHub отдаёт любому на публичный репозиторий — это не доступ."""
    assert has_explicit_access(_permissions(*granted)) is expected
