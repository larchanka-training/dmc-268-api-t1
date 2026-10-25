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
Постранично до `total_count`, не больше 50 страниц. Пропущенные установки —
одно предупреждение в логе с их id.

Подключение не обходит список: `user_repository` — `GET /repositories/{id}`
токеном пользователя (не виден — `None`), `app_installed` —
`GET /repos/{full_name}/installation` с JWT приложения (`404` или
приостановленная установка — `False`). Два запроса на подключение
(change `user-token-listing-edges`).

`401` — токен отозван или истёк: `ProviderTokenRejected`, сессию закрывает
use case. Сеть, `429`, `5xx`, `403` лимита (`x-ratelimit-remaining: 0`, `retry-after`
или «rate limit» в `message`) — `VcsUnavailableError`; прочие статусы и
неожиданное тело — `VcsError`: всё это
`502`, а не `500`. Повторов нет: список пользователь запросит заново сам.
Токен — только в заголовке запроса; в сообщениях об ошибках его нет.

Тела ответов разбирают приватные модели pydantic (`_GitHubModel`): строгие
типы (`null`, `bool` вместо числа, строка вместо числа — ошибка), лишние поля
GitHub игнорируются. Ошибка разбора — `VcsError` с путями полей и видами
ошибок, без значений. Наружу адаптер отдаёт только доменные типы.
"""

import logging
from collections.abc import Iterator
from datetime import datetime

import httpx
from pydantic import BaseModel, ConfigDict, ValidationError

from app.domain.entities import (
    InstallationRepository,
    RepositoryPermissions,
    Secret,
)
from app.domain.provider_token import ProviderTokenRejected
from app.infrastructure.oauth.bearer import BearerAuth
from app.infrastructure.vcs.errors import VcsAuthError, VcsError, VcsUnavailableError
from app.infrastructure.vcs.github_auth import GitHubAppAuth

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

    def __init__(self, client: httpx.Client, app_auth: GitHubAppAuth) -> None:
        """`client` — с `base_url` GitHub API; авторизация — токеном пользователя из
        каждого вызова, а проверка установки — JWT приложения из `app_auth`."""
        self._client = client
        self._app_auth = app_auth

    def list_repositories(self, access_token: Secret) -> list[InstallationRepository]:
        skipped: dict[str, list[int]] = {}
        repositories: list[InstallationRepository] = []
        try:
            for installation in self._installations(access_token):
                installation_id = installation.id
                if installation.suspended_at is not None:
                    skipped.setdefault("приостановлена", []).append(installation_id)
                    continue
                try:
                    for batch in self._installation_repositories(access_token, installation_id):
                        repositories.extend(batch)
                except _Inaccessible as failure:
                    # 404 — установка исчезла между запросами; 403 — например, SSO
                    # организации не авторизован для этого токена. Остальные
                    # установки пользователю всё равно видны.
                    skipped.setdefault(f"HTTP {failure.status}", []).append(installation_id)
        finally:
            if skipped:
                # Только id установок и причины: токена здесь нет.
                _log.warning("установки пропущены: %s", "; ".join(f"{why}: {ids}" for why, ids in skipped.items()))
        return repositories

    def user_repository(self, access_token: Secret, provider_id: str) -> InstallationRepository | None:
        """`GET /repositories/{id}` токеном пользователя («Get a repository»): `404` или
        `403` не из-за лимита — пользователь репозитория не видит."""
        if not (provider_id.isascii() and provider_id.isdigit()):
            # Id репозитория GitHub — число; иное не отправляем в путь запроса.
            # `isdigit()` один пропустил бы и не-ASCII цифры (`²`, `١٢٣`).
            return None
        url = f"/repositories/{provider_id}"
        try:
            return self._get(access_token, url, _Repository).to_domain()
        except _Inaccessible:
            return None

    def app_installed(self, full_name: str) -> bool:
        """`GET /repos/{owner}/{repo}/installation` с JWT приложения («Get a repository
        installation for the authenticated app»): `404` — не установлено,
        `suspended_at` не `null` — установка приостановлена, тоже «нет»."""
        url = f"/repos/{full_name}/installation"
        try:
            installation = self._get(None, url, _Installation)
        except _Inaccessible as failure:
            if failure.status == 404:
                return False
            raise VcsError(f"GitHub API: HTTP {failure.status} на GET {url}") from None
        return installation.suspended_at is None

    def _installations(self, access_token: Secret) -> Iterator[_Installation]:
        url = "/user/installations"
        try:
            for page in self._pages(access_token, url, _InstallationPage):
                yield from page.installations
        except _Inaccessible as failure:
            raise VcsError(f"GitHub API: HTTP {failure.status} на GET {url}") from None

    def _installation_repositories(self, access_token: Secret, installation_id: int) -> Iterator[list[InstallationRepository]]:
        url = f"/user/installations/{installation_id}/repositories"
        for page in self._pages(access_token, url, _RepositoryPage):
            yield [repository.to_domain() for repository in page.repositories]

    def _pages[P: _Page](self, access_token: Secret, url: str, model: type[P]) -> Iterator[P]:
        """Страницы ответа вида `{"total_count", <список>: [...]}`, лениво.

        Конец — набрано `total_count`, короткая или пустая страница; не больше
        `_MAX_PAGES` страниц: ответ, который не кончается, не держит запрос
        вечно. Упёрлись в предел — список усечён, и это видно в логе. Нужно
        только списку: подключение ищет репозиторий прямым запросом.
        """
        seen = 0
        for page_number in range(1, _MAX_PAGES + 1):
            page = self._get(access_token, f"{url}?per_page={_PER_PAGE}&page={page_number}", model)
            yield page
            seen += page.size
            if seen >= page.total_count or page.size < _PER_PAGE:
                return
        _log.warning("GET %s: больше %d страниц, список усечён", url, _MAX_PAGES)

    def _get[M: BaseModel](self, access_token: Secret | None, url: str, model: type[M]) -> M:
        """GET токеном пользователя или, без него, JWT приложения; тело — по `model`."""
        headers = {"Accept": _JSON_ACCEPT, "X-GitHub-Api-Version": _API_VERSION}
        # Токен — через `httpx.Auth`: открытого значения нет в локальных переменных.
        # JWT подписывается здесь: пустой или битый ключ App — `VcsAuthError`, `502`.
        auth = BearerAuth(access_token if access_token is not None else Secret(self._app_auth.app_jwt()))
        try:
            response = self._client.get(url, headers=headers, auth=auth)
        except httpx.RequestError as error:
            # `from None`: исходное исключение держит запрос с токеном в заголовке.
            raise VcsUnavailableError(f"GitHub API недоступен на GET {url}: {type(error).__name__}") from None
        status = response.status_code
        if status == 401 and access_token is None:
            raise VcsAuthError(f"GitHub API отверг JWT приложения на GET {url}")
        if status == 401:
            raise ProviderTokenRejected(f"GitHub API отверг токен пользователя на GET {url}")
        if status >= 500 or is_rate_limited(response):
            raise VcsUnavailableError(f"GitHub API: HTTP {status} на GET {url}")
        if status in (403, 404):
            raise _Inaccessible(status)
        if response.is_error:
            raise VcsError(f"GitHub API: HTTP {status} на GET {url}")
        try:
            return model.model_validate_json(response.content)
        except ValidationError as error:
            # Пути полей и виды ошибок, без значений: в теле могут быть данные пользователя.
            problems = ", ".join(
                f"{'.'.join(str(part) for part in item['loc']) or '<тело>'}: {item['type']}"
                for item in error.errors(include_url=False, include_input=False, include_context=False)
            )
            raise VcsError(f"GitHub API вернул неожиданное тело на GET {url}: {problems}") from None


def is_rate_limited(response: httpx.Response, *, graphql: bool = False) -> bool:
    """Отказ лимита GitHub, а не доступа: 429, 403 лимита, у GraphQL - ошибка лимита при 200.

    `403` лимита («Rate limits for the REST API»): первичный -
    `x-ratelimit-remaining: 0`, вторичный - `retry-after` или, бывает, только
    «rate limit» в `message` тела. GraphQL отдаёт превышение статусом 200
    (первичный) или 200/403 (вторичный) с сообщением в `errors` («Rate limits
    and query limits for the GraphQL API»); тип ошибки - `RATE_LIMITED`.
    """
    status = response.status_code
    if status == 429:
        return True
    if status == 403:
        if response.headers.get("x-ratelimit-remaining") == "0" or "retry-after" in response.headers:
            return True
        try:
            message = _ErrorBody.model_validate_json(response.content).message
        except ValidationError:
            return False
        return message is not None and "rate limit" in message.lower()
    if not graphql or status != 200:
        return False
    try:
        body = response.json()
    except ValueError:
        return False
    errors = body.get("errors") if isinstance(body, dict) else None
    if not isinstance(errors, list):
        return False
    return any(
        isinstance(item, dict)
        and (item.get("type") == "RATE_LIMITED" or "rate limit" in str(item.get("message", "")).lower())
        for item in errors
    )


class _GitHubModel(BaseModel):
    """Тело ответа GitHub: строгие типы, лишние поля игнорируются (их у GitHub много)."""

    model_config = ConfigDict(strict=True, extra="ignore", frozen=True)


class _Installation(_GitHubModel):
    """Установка из `GET /user/installations` и `GET /repos/{owner}/{repo}/installation`."""

    id: int
    # Нет поля — не приостановлена: в документации оно есть всегда, `null` у живой.
    suspended_at: datetime | None = None


class _Permissions(_GitHubModel):
    """Права пользователя на репозиторий. В примере ответа документации — только
    admin, push и pull; maintain и triage настоящий ответ несёт, нет поля — права нет."""

    admin: bool
    maintain: bool = False
    push: bool
    triage: bool = False
    pull: bool


class _Repository(_GitHubModel):
    id: int
    full_name: str
    default_branch: str
    private: bool
    permissions: _Permissions

    def to_domain(self) -> InstallationRepository:
        return InstallationRepository(
            provider_id=str(self.id),
            full_name=self.full_name,
            default_branch=self.default_branch,
            permissions=RepositoryPermissions(
                admin=self.permissions.admin,
                maintain=self.permissions.maintain,
                push=self.permissions.push,
                triage=self.permissions.triage,
            ),
            private=self.private,
        )


class _Page(_GitHubModel):
    total_count: int

    @property
    def size(self) -> int:
        raise NotImplementedError


class _InstallationPage(_Page):
    installations: list[_Installation]

    @property
    def size(self) -> int:
        return len(self.installations)


class _RepositoryPage(_Page):
    repositories: list[_Repository]

    @property
    def size(self) -> int:
        return len(self.repositories)


class _ErrorBody(BaseModel):
    """Тело ошибки GitHub — только `message`; разбор нестрогий: важен лишь текст."""

    model_config = ConfigDict(extra="ignore")

    message: str | None = None
