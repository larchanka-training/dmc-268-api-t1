"""Адаптер `InstallationCatalog` для GitHub REST API с токеном пользователя.

Порт не импортируется — совпадение структурное. Установки приложения,
доступные пользователю, — `GET /user/installations`, репозитории каждой —
`GET /user/installations/{installation_id}/repositories`, обе постранично
по 100. Обе ручки требуют user access token GitHub App и отдают ровно
пересечение «доступно приложению» и «доступно пользователю», у каждого
репозитория — `permissions` пользователя (документация GitHub REST,
«List app installations accessible to the user access token» и «List
repositories accessible to the user access token»). Доступ решает GitHub;
адаптер только переводит ответ (change `repositories-via-user-token`).

Приостановленная установка (`suspended_at` не `null`) пропускается: ревью
в ней всё равно не работают, и её отказ не должен ронять весь список.
Установка, исчезнувшая между запросами (`404` на её репозитории), — тоже.

`401` — токен отозван или истёк: `ProviderTokenRejected`, сессию закрывает
use case. Сеть, `429`, `5xx` — `VcsUnavailableError`; прочие статусы и
неожиданное тело, включая `null` в обязательных полях, — `VcsError`: всё это
`502`, а не `500`. Повторов нет: список пользователь запросит заново сам.
Токен — только в заголовке запроса; в сообщениях об ошибках его нет.
"""

from typing import Any

import httpx

from app.domain.entities import InstallationRepository, RepositoryPermissions, Secret
from app.domain.provider_token import ProviderTokenRejected
from app.infrastructure.vcs.errors import VcsError, VcsUnavailableError

_JSON_ACCEPT = "application/vnd.github+json"
_API_VERSION = "2022-11-28"
# Максимум страницы у GitHub: страница короче — последняя.
_PER_PAGE = 100


class _NotFound(Exception):
    """`404` на запросе: решает вызывающий метод адаптера."""


class GitHubInstallationCatalog:
    """Репозитории установок GitHub App, видимые владельцу токена, с его правами."""

    def __init__(self, client: httpx.Client) -> None:
        """`client` — с `base_url` GitHub API; авторизация — токеном из каждого вызова."""
        self._client = client

    def list_repositories(self, access_token: Secret) -> list[InstallationRepository]:
        repositories: list[InstallationRepository] = []
        for installation_id in self._installation_ids(access_token):
            try:
                repositories.extend(self._installation_repositories(access_token, installation_id))
            except _NotFound:
                continue
        return repositories

    def _installation_ids(self, access_token: Secret) -> list[int]:
        ids: list[int] = []
        try:
            pages = self._pages(access_token, "/user/installations", "installations")
        except _NotFound:
            raise VcsError("GitHub API: HTTP 404 на GET /user/installations") from None
        for page in pages:
            try:
                ids.extend(int(_strict(item, "id", int)) for item in page if item.get("suspended_at") is None)
            except (AttributeError, KeyError, TypeError) as error:
                raise VcsError(f"GitHub API вернул установку без id на GET /user/installations: {error!r}") from None
        return ids

    def _installation_repositories(self, access_token: Secret, installation_id: int) -> list[InstallationRepository]:
        url = f"/user/installations/{installation_id}/repositories"
        repositories: list[InstallationRepository] = []
        for page in self._pages(access_token, url, "repositories"):
            try:
                repositories.extend(_repository(item) for item in page)
            except (AttributeError, KeyError, TypeError) as error:
                raise VcsError(f"GitHub API вернул неожиданный репозиторий на GET {url}: {error!r}") from None
        return repositories

    def _pages(self, access_token: Secret, url: str, key: str) -> list[list[dict[str, Any]]]:
        """Страницы списка `key` из ответов вида `{"total_count", key: [...]}`."""
        pages: list[list[dict[str, Any]]] = []
        page = 1
        while True:
            body = self._get(access_token, f"{url}?per_page={_PER_PAGE}&page={page}")
            batch = body.get(key) if isinstance(body, dict) else None
            if not isinstance(batch, list) or not all(isinstance(item, dict) for item in batch):
                raise VcsError(f"GitHub API вернул неожиданное тело на GET {url}: нет массива {key}")
            pages.append(batch)
            if len(batch) < _PER_PAGE:
                return pages
            page += 1

    def _get(self, access_token: Secret, url: str) -> Any:
        headers = {
            "Authorization": f"Bearer {access_token.reveal()}",
            "Accept": _JSON_ACCEPT,
            "X-GitHub-Api-Version": _API_VERSION,
        }
        try:
            response = self._client.get(url, headers=headers)
        except httpx.RequestError as error:
            # `from None`: исходное исключение держит запрос с токеном в заголовке.
            raise VcsUnavailableError(f"GitHub API недоступен на GET {url}: {type(error).__name__}") from None
        if response.status_code == 401:
            raise ProviderTokenRejected(f"GitHub API отверг токен пользователя на GET {url}")
        if response.status_code == 404:
            raise _NotFound
        if response.status_code == 429 or response.status_code >= 500:
            raise VcsUnavailableError(f"GitHub API: HTTP {response.status_code} на GET {url}")
        if response.is_error:
            raise VcsError(f"GitHub API: HTTP {response.status_code} на GET {url}")
        try:
            return response.json()
        except ValueError:
            raise VcsError(f"GitHub API вернул не-JSON на GET {url}") from None


def _repository(item: dict[str, Any]) -> InstallationRepository:
    permissions = item["permissions"]
    if not isinstance(permissions, dict):
        raise TypeError("permissions — не объект")
    return InstallationRepository(
        provider_id=str(_strict(item, "id", int)),
        full_name=_strict(item, "full_name", str),
        default_branch=_strict(item, "default_branch", str),
        permissions=RepositoryPermissions(
            admin=_strict(permissions, "admin", bool),
            # В примере ответа документации у `permissions` только admin, push и
            # pull; настоящий ответ несёт и maintain. Нет поля — права maintain нет.
            maintain=_strict(permissions, "maintain", bool) if "maintain" in permissions else False,
        ),
    )


def _strict[T](item: dict[str, Any], name: str, kind: type[T]) -> T:
    """Поле ответа ровно этого типа; `null`, `bool` вместо числа и прочее — `TypeError`."""
    value = item[name]
    if not isinstance(value, kind) or (kind is int and isinstance(value, bool)):
        raise TypeError(f"{name} — не {kind.__name__}")
    return value
