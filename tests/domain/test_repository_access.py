"""Кто может подключить репозиторий — по `permissions` из ответа GitHub."""

from datetime import UTC, datetime
from uuid import UUID

import pytest

from app.domain.entities import (
    InstallationRepository,
    Repository,
    RepositoryPermissions,
)
from app.domain.enums import Provider, ReviewPause
from app.domain.repository_access import (
    can_connect,
    has_explicit_access,
    review_pause,
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


_NOW = datetime(2026, 10, 31, tzinfo=UTC)


@pytest.mark.parametrize(
    ("connected_at", "auto_review_enabled", "expected"),
    [
        (_NOW, True, None),
        (_NOW, False, ReviewPause.AUTO_REVIEW_OFF),
        (None, True, ReviewPause.DISCONNECTED),
        (None, False, ReviewPause.DISCONNECTED),
    ],
)
def test_disconnection_outranks_auto_review_setting_as_review_pause(
    connected_at: datetime | None, auto_review_enabled: bool, expected: ReviewPause | None
) -> None:
    repository = Repository(
        id=UUID(int=1),
        provider=Provider.GITHUB,
        provider_id="987654",
        full_name="owner/repo",
        default_branch="main",
        auto_review_enabled=auto_review_enabled,
        created_at=_NOW,
        updated_at=_NOW,
        connected_at=connected_at,
    )

    assert review_pause(repository) is expected
