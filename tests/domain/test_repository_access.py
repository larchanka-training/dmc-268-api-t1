"""Кто может подключить репозиторий — по `permissions` из ответа GitHub."""

import pytest

from app.domain.entities import InstallationRepository, RepositoryPermissions
from app.domain.repository_access import (
    can_connect,
    has_explicit_access,
    to_connect_on_setup,
    visible_for_connect,
)


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


@pytest.mark.parametrize(
    ("private", "granted", "expected"),
    [(True, (), True), (False, (), False), (False, ("push",), True), (True, ("admin",), True)],
)
def test_a_private_repository_is_visible_with_read_a_public_one_is_not(private: bool, granted: tuple[str, ...], expected: bool) -> None:
    """Приватный GitHub отдаёт только участнику — роль read уже доступ."""
    repository = InstallationRepository("1", "octo-org/repo", "main", _permissions(*granted), private=private)
    assert visible_for_connect(repository) is expected


def test_setup_connects_only_manageable_repositories_without_a_record() -> None:
    """Admin или maintain и записи нет; есть запись (в том числе выключенная) — не трогаем."""
    admin = InstallationRepository("1", "o/admin", "main", _permissions("admin"))
    maintain = InstallationRepository("2", "o/maintain", "main", _permissions("maintain"))
    write = InstallationRepository("3", "o/write", "main", _permissions("push"))
    registered = InstallationRepository("4", "o/registered", "main", _permissions("admin"))

    assert to_connect_on_setup([admin, maintain, write, registered], {"4"}) == [admin, maintain]
