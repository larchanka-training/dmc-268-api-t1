"""Фейк порта `InstallationCatalog`: репозитории, видимые пользователю, — из литералов теста."""

from app.domain.entities import InstallationRepository, Secret


class FakeInstallationCatalog:
    def __init__(
        self,
        repositories: list[InstallationRepository] | None = None,
        error: Exception | None = None,
    ) -> None:
        # То, что GitHub отдал бы по токену пользователя: невидимых тут нет.
        self._repositories = list(repositories or [])
        # `VcsError` — сбой GitHub, `ProviderTokenRejected` — `401` на токен.
        self._error: Exception | None = error
        self.tokens: list[Secret] = []

    def list_repositories(self, access_token: Secret) -> list[InstallationRepository]:
        self.tokens.append(access_token)
        if self._error is not None:
            raise self._error
        return list(self._repositories)

