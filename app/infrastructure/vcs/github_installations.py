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
Установка, исчезнувшая между запросами или закрытая для токена (`404` или
`403` на её репозитории, например SSO организации не авторизован), — тоже.
Постранично до `total_count`, не больше 50 страниц.

`401` — токен отозван или истёк: `ProviderTokenRejected`, сессию закрывает
use case. Сеть, `429`, `5xx`, `403` лимита (`x-ratelimit-remaining: 0` или
`retry-after`) — `VcsUnavailableError`; прочие статусы и
неожиданное тело, включая `null` в обязательных полях, — `VcsError`: всё это
`502`, а не `500`. Повторов нет: список пользователь запросит заново сам.
Токен — только в заголовке запроса; в сообщениях об ошибках его нет.
"""

import logging
from typing import Any

import httpx

from app.domain.entities import InstallationRepository, RepositoryPermissions, Secret
from app.domain.provider_token import ProviderTokenRejected
from app.infrastructure.oauth.bearer import BearerAuth
from app.infrastructure.vcs.errors import VcsError, VcsUnavailableError

_JSON_ACCEPT = "application/vnd.github+json"
_API_VERSION = "2022-11-28"
# Максимум страницы у GitHub: страница короче — последняя.
_PER_PAGE = 100
# Предел страниц одного списка: 5000 установок или репозиториев одной установки.
_MAX_PAGES = 50

_log = logging.getLogger(__name__)


class _Inaccessible(Exception):
    """`403` (не лимит) или `404` на запросе: решает вызывающий метод адаптера."""

    def __init__(self, status: int) -> None:
        super().__init__(status)
        self.status = status


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
            except _Inaccessible as skipped:
                # 404 — установка исчезла между запросами; 403 — например, SSO
                # организации не авторизован для этого токена. Остальные
                # установки пользователю всё равно видны.
                _log.info("установка %s пропущена: HTTP %s", installation_id, skipped.status)
                continue
        return repositories

    def _installation_ids(self, access_token: Secret) -> list[int]:
        ids: list[int] = []
        try:
            pages = self._pages(access_token, "/user/installations", "installations")
        except _Inaccessible as failure:
            raise VcsError(f"GitHub API: HTTP {failure.status} на GET /user/installations") from None
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
        """Страницы списка `key` из ответов вида `{"total_count", key: [...]}`.

        Конец — набрано `total_count`, короткая или пустая страница; не больше
        `_MAX_PAGES` страниц: ответ, который не кончается, не держит запрос
        вечно. Упёрлись в предел — список усечён, и это видно в логе.
        """
        pages: list[list[dict[str, Any]]] = []
        seen = 0
        for page in range(1, _MAX_PAGES + 1):
            body = self._get(access_token, f"{url}?per_page={_PER_PAGE}&page={page}")
            batch = body.get(key) if isinstance(body, dict) else None
            if not isinstance(batch, list) or not all(isinstance(item, dict) for item in batch):
                raise VcsError(f"GitHub API вернул неожиданное тело на GET {url}: нет массива {key}")
            total = body.get("total_count")
            if not isinstance(total, int) or isinstance(total, bool):
                raise VcsError(f"GitHub API вернул неожиданное тело на GET {url}: нет total_count")
            pages.append(batch)
            seen += len(batch)
            if seen >= total or len(batch) < _PER_PAGE:
                return pages
        _log.warning("GET %s: больше %d страниц, список усечён", url, _MAX_PAGES)
        return pages

    def _get(self, access_token: Secret, url: str) -> Any:
        headers = {"Accept": _JSON_ACCEPT, "X-GitHub-Api-Version": _API_VERSION}
        try:
            # Токен — через `httpx.Auth`: открытого значения нет в локальных переменных.
            response = self._client.get(url, headers=headers, auth=BearerAuth(access_token))
        except httpx.RequestError as error:
            # `from None`: исходное исключение держит запрос с токеном в заголовке.
            raise VcsUnavailableError(f"GitHub API недоступен на GET {url}: {type(error).__name__}") from None
        status = response.status_code
        if status == 401:
            raise ProviderTokenRejected(f"GitHub API отверг токен пользователя на GET {url}")
        if status == 429 or status >= 500 or (status == 403 and _rate_limited(response)):
            raise VcsUnavailableError(f"GitHub API: HTTP {status} на GET {url}")
        if status in (403, 404):
            raise _Inaccessible(status)
        if response.is_error:
            raise VcsError(f"GitHub API: HTTP {status} на GET {url}")
        try:
            return response.json()
        except ValueError:
            raise VcsError(f"GitHub API вернул не-JSON на GET {url}") from None


def _rate_limited(response: httpx.Response) -> bool:
    """`403` из-за лимита GitHub, а не из-за доступа: так GitHub помечает первичный
    и вторичный лимиты («Rate limits for the REST API»)."""
    return response.headers.get("x-ratelimit-remaining") == "0" or "retry-after" in response.headers


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
