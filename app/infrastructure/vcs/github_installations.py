"""Адаптер `InstallationCatalog` для GitHub REST API.

Порт не импортируется — совпадение структурное. Установки приложения
перечисляются с JWT приложения (`GET /app/installations`), репозитории
каждой — с её installation token (`GET /installation/repositories`), права
пользователя — `GET /repos/{full_name}/collaborators/{login}/permission`;
для него приложению хватает разрешения Metadata: read. `installation_id`
не хранится: его даёт список установок (change `implement-repository-connect`,
design D1).

Запросы с installation token идут через `_get` шлюза — тот же backoff на
429/5xx, сброс отозванного токена на 401 и перевод ошибок в `VcsError`, что у
`GitHubReviewClient`. Запрос с JWT не повторяется: JWT свежий на каждый
вызов, а сбой списка установок пользователь повторит сам.
"""

from typing import Any

import httpx

from app.domain.entities import InstallationRepository
from app.domain.enums import RepositoryRole
from app.infrastructure.vcs.errors import (
    VcsAuthError,
    VcsError,
    VcsNotFoundError,
    VcsUnavailableError,
)
from app.infrastructure.vcs.github import GitHubVcsGateway
from app.infrastructure.vcs.github_auth import GitHubAppAuth

_JSON_ACCEPT = "application/vnd.github+json"
# Максимум страницы у GitHub: страница короче — последняя.
_PER_PAGE = 100
# Ответ «доступа нет» у GitHub в `role_name` и `permission`.
_NO_ACCESS = "none"
_KNOWN_ROLES = {role.value: role for role in RepositoryRole if role is not RepositoryRole.CUSTOM}


def _translate_role(role_name: str | None, permission: str) -> RepositoryRole | None:
    """Роль из ответа GitHub «Get repository permissions for a user».

    `permission` — устаревшие базовые роли admin, write, read, none: maintain в
    нём отдаётся как write, triage — как read. Полное имя роли, включая
    пользовательские, — в `role_name`; без него — `permission`. Незнакомое имя —
    пользовательская роль: доступ есть, что он разрешает — неизвестно.
    """
    name = role_name if role_name is not None else permission
    if name == _NO_ACCESS:
        return None
    return _KNOWN_ROLES.get(name, RepositoryRole.CUSTOM)


def _strict_bool(value: object) -> bool:
    """`private` из ответа GitHub; не булево — неожиданное тело, а не «публичный»."""
    if not isinstance(value, bool):
        raise TypeError("private — не булево")
    return value


class GitHubInstallationCatalog:
    """Репозитории установок GitHub App и права пользователя на них."""

    def __init__(self, auth: GitHubAppAuth, client: httpx.Client, gateway: GitHubVcsGateway) -> None:
        """Клиент и `auth` — те же, что у шлюза: кэш installation tokens общий."""
        self._auth = auth
        self._client = client
        self._gateway = gateway

    def list_repositories(self) -> list[InstallationRepository]:
        repositories: list[InstallationRepository] = []
        for installation_id in self._installation_ids():
            repositories.extend(self._installation_repositories(installation_id))
        return repositories

    def user_role(self, repository: InstallationRepository, login: str) -> RepositoryRole | None:
        # У приватного репозитория ответ ручки прав для постороннего не равен
        # 404 или `none` — на стенде он давал доступ к чужим приватным
        # репозиториям. Поэтому сначала прямая проверка «есть ли у пользователя
        # доступ»: 204 — есть, 404 — нет (change `repository-visibility-private`).
        if repository.private and not self._is_collaborator(repository, login):
            return None
        url = f"/repos/{repository.full_name}/collaborators/{login}/permission"
        try:
            response = self._gateway._get(url, repository.installation_id, _JSON_ACCEPT)
        except VcsNotFoundError:
            # Не коллаборатор (или GitHub не раскрывает репозиторий) — доступа нет.
            return None
        try:
            data: dict[str, Any] = response.json()
            role_name = data.get("role_name")
            permission = data["permission"]
            if not isinstance(permission, str) or not isinstance(role_name, str | None):
                raise TypeError("роль — не строка")
        except (ValueError, KeyError, TypeError, AttributeError) as error:
            raise VcsError(f"GitHub API вернул неожиданное тело на GET {url}: {error!r}") from error
        return _translate_role(role_name, permission)

    def _is_collaborator(self, repository: InstallationRepository, login: str) -> bool:
        url = f"/repos/{repository.full_name}/collaborators/{login}"
        try:
            response = self._gateway._get(url, repository.installation_id, _JSON_ACCEPT)
        except VcsNotFoundError:
            return False
        if response.status_code != 204:
            raise VcsError(f"GitHub API: HTTP {response.status_code} вместо 204 на GET {url}")
        return True

    def _installation_ids(self) -> list[int]:
        ids: list[int] = []
        page = 1
        while True:
            url = f"/app/installations?per_page={_PER_PAGE}&page={page}"
            data = self._get_as_app(url)
            if not isinstance(data, list):
                raise VcsError(f"GitHub API вернул не массив на GET {url}")
            try:
                ids.extend(int(item["id"]) for item in data)
            except (KeyError, TypeError, ValueError) as error:
                raise VcsError(f"GitHub API вернул установку без id на GET {url}") from error
            if len(data) < _PER_PAGE:
                return ids
            page += 1

    def _installation_repositories(self, installation_id: int) -> list[InstallationRepository]:
        repositories: list[InstallationRepository] = []
        page = 1
        while True:
            url = f"/installation/repositories?per_page={_PER_PAGE}&page={page}"
            response = self._gateway._get(url, installation_id, _JSON_ACCEPT)
            try:
                batch: list[dict[str, Any]] = response.json()["repositories"]
                repositories.extend(
                    InstallationRepository(
                        provider_id=str(item["id"]),
                        full_name=item["full_name"],
                        default_branch=item["default_branch"],
                        installation_id=installation_id,
                        private=_strict_bool(item["private"]),
                    )
                    for item in batch
                )
            except (ValueError, KeyError, TypeError) as error:
                raise VcsError(f"GitHub API вернул неожиданное тело на GET {url}: {error!r}") from error
            if len(batch) < _PER_PAGE:
                return repositories
            page += 1

    def _get_as_app(self, url: str) -> Any:
        """GET от имени приложения (JWT) с переводом сбоев в `VcsError`."""
        headers = {"Authorization": f"Bearer {self._auth.app_jwt()}", "Accept": _JSON_ACCEPT}
        try:
            response = self._client.get(url, headers=headers)
        except httpx.RequestError as error:
            raise VcsUnavailableError(f"GitHub API недоступен на GET {url}: {error}") from error
        if response.status_code == 401:
            raise VcsAuthError(f"GitHub API отклонил JWT приложения на GET {url}")
        if response.status_code == 429 or response.status_code >= 500:
            raise VcsUnavailableError(f"GitHub API: HTTP {response.status_code} на GET {url}")
        if response.is_error:
            raise VcsError(f"GitHub API: HTTP {response.status_code} на GET {url}")
        try:
            return response.json()
        except ValueError as error:
            raise VcsError(f"GitHub API вернул не-JSON на GET {url}") from error
