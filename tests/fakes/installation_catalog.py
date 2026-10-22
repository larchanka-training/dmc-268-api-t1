"""Фейк порта `InstallationCatalog`: репозитории установок и роли — из литералов теста."""

from app.domain.entities import InstallationRepository
from app.domain.enums import RepositoryRole
from app.domain.vcs_errors import VcsError


class FakeInstallationCatalog:
    def __init__(
        self,
        repositories: list[InstallationRepository] | None = None,
        managers: set[tuple[str, str]] | None = None,
        error: VcsError | None = None,
        permission_error: VcsError | None = None,
        roles: dict[tuple[str, str], RepositoryRole] | None = None,
    ) -> None:
        self._repositories = list(repositories or [])
        # Роли по паре (full_name, login); `managers` — короткая запись роли admin.
        self._roles = {pair: RepositoryRole.ADMIN for pair in managers or set()} | dict(roles or {})
        self._error = error
        # Сбой только запроса роли: список установок при этом отвечает.
        self._permission_error = permission_error
        self.permission_checks: list[tuple[str, str]] = []

    def list_repositories(self) -> list[InstallationRepository]:
        if self._error is not None:
            raise self._error
        return list(self._repositories)

    def user_role(self, repository: InstallationRepository, login: str) -> RepositoryRole | None:
        error = self._error or self._permission_error
        if error is not None:
            raise error
        self.permission_checks.append((repository.full_name, login))
        return self._roles.get((repository.full_name, login))
