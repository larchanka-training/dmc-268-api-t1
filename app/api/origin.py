"""Проверка `Origin` изменяющих запросов — защита cookie-сессии от CSRF.

`POST`, `PUT`, `PATCH` и `DELETE` под `/api/v1/`, кроме приёма вебхуков, проходят, только
если `Origin` совпадает с публичным адресом сервиса; иначе — `403 ORIGIN_FORBIDDEN`.
Проверка — middleware, а не зависимость роута: FastAPI разбирает JSON тела раньше
зависимостей, и битое тело с чужого сайта получило бы `422` вместо `403`. Здесь отказ
наступает до роутинга, разбора тела, сессии и базы. Вебхук подтверждает подпись, `Origin`
у него нет.

Ответ строит общий `problem_response`: middleware стоит внутри `TraceIdMiddleware`, поэтому
`trace_id` у запроса уже есть. Чистый ASGI, а не `BaseHTTPMiddleware`, — как `trace_id.py`:
тот оборачивает тело ответа и мешает потоку SSE.
"""

from http import HTTPStatus

from starlette.datastructures import Headers
from starlette.requests import Request
from starlette.types import ASGIApp, Receive, Scope, Send

from app.api.errors import problem_response
from app.api.problems import ErrorCode

UNSAFE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})
_GUARDED_PREFIX = "/api/v1/"
# Приём вебхука (`app/api/webhooks.py`): без Origin и со своим лимитом тела (`body_limit.py`).
WEBHOOKS_PREFIX = "/api/v1/webhooks/"
_DETAIL = "Изменяющий запрос пришёл без Origin или с Origin другого сайта."


def origin_required(method: str, path: str) -> bool:
    """Нужна ли запросу проверка `Origin`."""
    return (
        method in UNSAFE_METHODS
        and path.startswith(_GUARDED_PREFIX)
        and not path.startswith(WEBHOOKS_PREFIX)
    )


class OriginMiddleware:
    def __init__(self, app: ASGIApp, public_origin: str) -> None:
        self.app = app
        self.public_origin = public_origin

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http" and origin_required(scope["method"], scope["path"]):
            origin = Headers(scope=scope).get("origin")
            # Пустой публичный адрес не совпадает ни с чем: изменяющие запросы закрыты.
            if not self.public_origin or origin != self.public_origin:
                response = problem_response(Request(scope), HTTPStatus.FORBIDDEN, ErrorCode.ORIGIN_FORBIDDEN, _DETAIL)
                await response(scope, receive, send)
                return
        await self.app(scope, receive, send)
