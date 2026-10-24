"""Адаптер `WebhookDeliveries` для GitHub REST API с JWT приложения (#149).

Порт не импортируется — совпадение структурное. Документация GitHub REST
(версия API 2022-11-28), «Webhooks» приложения:

- «List deliveries for an app webhook», `GET /app/hook/deliveries` — массив
  попыток, новые первыми, до 100 на страницу; следующая страница — курсор в
  заголовке `Link` (`rel="next"`). Фильтр `status=failure` не передаём: решению
  нужны и успешные попытки (не повторять уже доставленное), а `failure` у
  GitHub — только коды 400–599, без тайм-аутов.
- «Get a delivery for an app webhook», `GET /app/hook/deliveries/{delivery_id}` —
  попытка с `request.payload` (объект или `null`): по нему проход повтора
  сверяет коммит доставки с головой PR. Читается только `request.payload`,
  остальное тело (заголовки, ответ нашего сервиса) не разбирается.
- «Redeliver a delivery for an app webhook»,
  `POST /app/hook/deliveries/{delivery_id}/attempts` — `202`.

Все — JWT приложения (`GitHubAppAuth.app_jwt`), не installation token.
Листание кончается на странице, дошедшей до `since`, без `next` или на
`_MAX_PAGES` страницах. Ссылку `next` на чужой хост не открываем: JWT уходит
только на хост API.

Отказы — как у `GitHubInstallationCatalog`: сеть, `429`, `5xx`, `403` лимита —
`VcsUnavailableError` (временный, проход повтора останавливается); `401` и
неподписываемый ключ — `VcsAuthError`; прочие статусы и неожиданное тело —
`VcsError`. Повторов внутри адаптера нет: следующий проход повторит сам.
"""

import logging
from datetime import datetime

import httpx
from pydantic import AwareDatetime, BaseModel, ConfigDict, TypeAdapter, ValidationError

from app.domain.entities import Secret
from app.domain.webhook_redelivery import WebhookDelivery
from app.infrastructure.oauth.bearer import BearerAuth
from app.infrastructure.vcs.errors import VcsAuthError, VcsError, VcsUnavailableError
from app.infrastructure.vcs.github_auth import GitHubAppAuth
from app.infrastructure.vcs.github_installations import is_rate_limited

_JSON_ACCEPT = "application/vnd.github+json"
_API_VERSION = "2022-11-28"
_DELIVERIES = "/app/hook/deliveries"
# Максимум страницы у GitHub.
_PER_PAGE = 100
# Предел страниц за проход: 1000 попыток — больше, чем набирается за трое суток
# у нашего числа репозиториев; упёрлись — видно в логе.
_MAX_PAGES = 10

_log = logging.getLogger(__name__)


class GitHubWebhookDeliveries:
    """Доставки вебхука GitHub App: список попыток, payload попытки и переотправка."""

    def __init__(self, client: httpx.Client, app_auth: GitHubAppAuth) -> None:
        """`client` — с `base_url` GitHub API; сети в конструкторе нет."""
        self._client = client
        self._app_auth = app_auth

    def list_recent(self, since: datetime) -> list[WebhookDelivery]:
        recent: list[WebhookDelivery] = []
        url: str | None = f"{_DELIVERIES}?per_page={_PER_PAGE}"
        for _ in range(_MAX_PAGES):
            assert url is not None
            response = self._send("GET", url)
            try:
                page = _PAGE.validate_json(response.content)
            except ValidationError as error:
                raise VcsError(f"GitHub API вернул неожиданное тело на GET {_DELIVERIES}: {_problems(error)}") from None
            recent.extend(item.to_domain() for item in page if item.delivered_at >= since)
            url = response.links.get("next", {}).get("url")
            if not page or page[-1].delivered_at < since or url is None:
                return recent
            if httpx.URL(url).host != self._client.base_url.host:
                raise VcsError(f"GitHub API: ссылка next на GET {_DELIVERIES} ведёт на чужой хост")
        _log.warning("GET %s: больше %d страниц, список доставок усечён", _DELIVERIES, _MAX_PAGES)
        return recent

    def delivery_payload(self, delivery_id: int) -> dict[str, object] | None:
        response = self._send("GET", f"{_DELIVERIES}/{delivery_id}")
        try:
            return _Detail.model_validate_json(response.content).request.payload
        except ValidationError as error:
            raise VcsError(f"GitHub API вернул неожиданное тело на GET {_DELIVERIES}/{{id}}: {_problems(error)}") from None

    def redeliver(self, delivery_id: int) -> None:
        self._send("POST", f"{_DELIVERIES}/{delivery_id}/attempts")

    def _send(self, method: str, url: str) -> httpx.Response:
        """Запрос с JWT приложения; не-2xx — исключение по виду отказа."""
        headers = {"Accept": _JSON_ACCEPT, "X-GitHub-Api-Version": _API_VERSION}
        # JWT подписывается здесь: пустой или битый ключ App — `VcsAuthError` до запроса.
        auth = BearerAuth(Secret(self._app_auth.app_jwt()))
        path = httpx.URL(url).path
        try:
            response = self._client.request(method, url, headers=headers, auth=auth)
        except httpx.RequestError as error:
            # `from None`: исходное исключение держит запрос с JWT в заголовке.
            raise VcsUnavailableError(f"GitHub API недоступен на {method} {path}: {type(error).__name__}") from None
        status = response.status_code
        if status == 401:
            raise VcsAuthError(f"GitHub API отверг JWT приложения на {method} {path}")
        if status == 429 or status >= 500 or (status == 403 and is_rate_limited(response)):
            raise VcsUnavailableError(f"GitHub API: HTTP {status} на {method} {path}")
        if response.is_error:
            raise VcsError(f"GitHub API: HTTP {status} на {method} {path}")
        return response


class _Delivery(BaseModel):
    """«Simple webhook delivery»: строгие типы, лишние поля GitHub игнорируются."""

    model_config = ConfigDict(strict=True, extra="ignore", frozen=True)

    id: int
    guid: str
    delivered_at: AwareDatetime
    redelivery: bool
    status: str
    status_code: int
    event: str
    action: str | None

    def to_domain(self) -> WebhookDelivery:
        return WebhookDelivery(
            id=self.id,
            guid=self.guid,
            delivered_at=self.delivered_at,
            redelivery=self.redelivery,
            status=self.status,
            status_code=self.status_code,
            event=self.event,
            action=self.action,
        )


_PAGE = TypeAdapter(list[_Delivery])


class _DeliveryRequest(BaseModel):
    """`request` попытки: тело события как прислал GitHub; разбирает его приём-домен."""

    model_config = ConfigDict(strict=True, extra="ignore", frozen=True)

    payload: dict[str, object] | None


class _Detail(BaseModel):
    """«Webhook delivery» из `GET /app/hook/deliveries/{delivery_id}` — только `request`."""

    model_config = ConfigDict(strict=True, extra="ignore", frozen=True)

    request: _DeliveryRequest


def _problems(error: ValidationError) -> str:
    """Пути полей и виды ошибок, без значений."""
    return ", ".join(
        f"{'.'.join(str(part) for part in item['loc']) or '<тело>'}: {item['type']}"
        for item in error.errors(include_url=False, include_input=False, include_context=False)
    )
