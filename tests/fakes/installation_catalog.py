"""Фейк порта `InstallationCatalog`: репозитории, видимые пользователю, — из литералов теста."""

from collections.abc import Mapping

from app.domain.entities import InstallationRepository, Secret
from app.domain.enums import RepositorySelection


class FakeInstallationCatalog:
    def __init__(
        self,
        repositories: list[InstallationRepository] | None = None,
        error: Exception | None = None,
        *,
        not_installed: set[str] | None = None,
        installations: dict[int, tuple[RepositorySelection, list[InstallationRepository]]] | None = None,
        installation_ids: dict[str, int] | None = None,
        installation_id_error: Exception | None = None,
        repositories_by_token: Mapping[str, list[InstallationRepository]] | None = None,
    ) -> None:
        # То, что GitHub отдал бы по токену пользователя: невидимых тут нет.
        self._repositories = list(repositories or [])
        # Свой набор видимых для токена — как GitHub: ответ зависит от токена; нет токена — общий набор.
        self._repositories_by_token = {token: list(found) for token, found in (repositories_by_token or {}).items()}
        # `VcsError` — сбой GitHub, `ProviderTokenRejected` — `401` на токен.
        self._error: Exception | None = error
        # `full_name` репозиториев, где приложение не установлено или приостановлено.
        self._not_installed = set(not_installed or ())
        # Установки пользователя для Setup URL: id — выбор и репозитории установки.
        self._installations = dict(installations or {})
        # Номер установки по `full_name`; не задан — `1` у любого установленного репозитория.
        # Публичный: тест меняет номер посреди сценария — переустановка приложения.
        self.installation_ids = dict(installation_ids or {})
        # Сбой только запроса номера установки: видимость при этом считается.
        self._installation_id_error = installation_id_error
        self.tokens: list[Secret] = []
        self.calls: list[str] = []

    def list_repositories(self, access_token: Secret) -> list[InstallationRepository]:
        self._record("list_repositories", access_token)
        return list(self._repositories_by_token.get(access_token.reveal(), self._repositories))

    def user_repository(self, access_token: Secret, provider_id: str) -> InstallationRepository | None:
        self._record("user_repository", access_token)
        # GitHub разбирает id как число: `/repositories/0101` — тот же репозиторий, что `/101`.
        if not (provider_id.isascii() and provider_id.isdigit()):
            return None
        return next((r for r in self._repositories if r.provider_id == str(int(provider_id))), None)

    def installation_selection(self, access_token: Secret, installation_id: int) -> RepositorySelection | None:
        self._record("installation_selection", access_token)
        found = self._installations.get(installation_id)
        return found[0] if found is not None else None

    def installation_repositories(self, access_token: Secret, installation_id: int) -> list[InstallationRepository]:
        self._record("installation_repositories", access_token)
        return list(self._installations[installation_id][1])

    def app_installed(self, full_name: str) -> bool:
        self.calls.append("app_installed")
        if self._error is not None:
            raise self._error
        return full_name not in self._not_installed

    def repository_installation_id(self, full_name: str) -> int | None:
        self.calls.append("repository_installation_id")
        error = self._installation_id_error or self._error
        if error is not None:
            raise error
        if full_name in self._not_installed:
            return None
        return self.installation_ids.get(full_name, 1)

    def _record(self, call: str, access_token: Secret) -> None:
        self.calls.append(call)
        self.tokens.append(access_token)
        if self._error is not None:
            raise self._error
