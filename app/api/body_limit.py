"""Лимит тела запроса: тело больше `REQUEST_BODY_MAX_BYTES` не читается целиком (#72).

Приём вебхука открыт без сессии и читает сырое тело до проверки подписи — без лимита
любой мог бы занять память процесса многогигабайтным телом. Ручки с JSON-телом читают
его до проверки сессии. Поэтому лимит стоит до роутинга, разбора тела, сессии и подписи:

- `Content-Length` больше лимита — `413` сразу, тело у сервера не запрашивается;
- без `Content-Length` и `Transfer-Encoding` тела по HTTP/1.1 нет — запрос идёт мимо;
- иначе тело читается здесь, с подсчётом байтов, и на куске, где сумма перешла лимит,
  чтение обрывается — так ограничен и поток без `Content-Length` (chunked), и поток длиннее
  объявленной длины. Дочитанное в пределах лимита приложение получает одним сообщением.

Сжатое тело (`Content-Encoding`) приложение не распаковывает: лимит считает те же байты,
что потом лежат в памяти, и сжатая «бомба» его не обходит.

Ответ строит общий `problem_response`, как у `OriginMiddleware`: middleware стоит внутри
`TraceIdMiddleware`, `trace_id` у запроса уже есть. Встроенный `max_body_size` Starlette
не годится: отказ по `Content-Length` он отдаёт `text/plain` и подменяет им уже собранный
ответ, без Problem Details и `X-Trace-ID`. Исключение из `receive` тоже не годится: FastAPI
превращает сбой чтения тела ручки с JSON-телом в `400`. Тело после отказа не дочитано,
поэтому ответ несёт `Connection: close` — соединение не переиспользуется.

Чистый ASGI, а не `BaseHTTPMiddleware`, — как `trace_id.py`: тот мешает потоку SSE.
"""

from http import HTTPStatus

from starlette.datastructures import Headers
from starlette.requests import Request
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.api.errors import problem_response
from app.api.problems import ErrorCode


class RequestBodyLimitMiddleware:
    def __init__(self, app: ASGIApp, max_bytes: int) -> None:
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        headers = Headers(scope=scope)
        declared = _declared_length(headers)
        if declared is not None and declared > self.max_bytes:
            await self._reject(scope, receive, send)
            return
        if not declared and "transfer-encoding" not in headers:
            # Ни длины, ни chunked: по HTTP/1.1 (RFC 9112, 6.3) тела у запроса нет. GET и
            # поток SSE идут мимо как есть: ждать тела, которого нет, здесь незачем.
            await self.app(scope, receive, send)
            return
        chunks: list[bytes] = []
        size = 0
        while True:
            message = await receive()
            if message["type"] != "http.request":
                # Клиент ушёл, не дослав тело: приложение увидит это само, как без лимита.
                first: Message = message
                break
            body = message.get("body", b"")
            size += len(body)
            if size > self.max_bytes:
                await self._reject(scope, receive, send)
                return
            chunks.append(body)
            if not message.get("more_body", False):
                first = {"type": "http.request", "body": b"".join(chunks), "more_body": False}
                break
        pending = [first]

        async def replay() -> Message:
            # Тело — одним сообщением, дальше — сервер как есть (`http.disconnect` для SSE).
            if pending:
                return pending.pop()
            return await receive()

        await self.app(scope, replay, send)

    async def _reject(self, scope: Scope, receive: Receive, send: Send) -> None:
        response = problem_response(
            Request(scope),
            HTTPStatus.CONTENT_TOO_LARGE,
            ErrorCode.REQUEST_BODY_TOO_LARGE,
            f"Тело запроса больше лимита {self.max_bytes} байт.",
            headers={"Connection": "close"},
        )
        await response(scope, receive, send)


def _declared_length(headers: Headers) -> int | None:
    """`Content-Length` запроса; нечисловой оставляет решение подсчёту байтов."""
    value = headers.get("content-length")
    if value is None or not value.isdigit():
        return None
    return int(value)
