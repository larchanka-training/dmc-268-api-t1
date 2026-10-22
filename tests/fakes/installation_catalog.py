"""Фейк порта `InstallationCatalog`: репозитории установок и права — из литералов теста."""

from app.domain.entities import InstallationRepository
from app.domain.vcs_errors import VcsError


class FakeInstallationCatalog:
    def __init__(
        self,
        repositories: list[InstallationRepository] | None = None,
        admins: set[tuple[str, str]] | None = None,
        error: VcsError | None = None,
    ) -> None:
        self._repositories = list(repositories or [])
        # Пары (full_name, login) с правами admin.
        self._admins = set(admins or set())
        self._error = error
        self.permission_checks: list[tuple[str, str]] = []

    def list_repositories(self) -> list[InstallationRepository]:
        if self._error is not None:
            raise self._error
        return list(self._repositories)

    def is_admin(self, repository: InstallationRepository, login: str) -> bool:
        if self._error is not None:
            raise self._error
        self.permission_checks.append((repository.full_name, login))
        return (repository.full_name, login) in self._admins
