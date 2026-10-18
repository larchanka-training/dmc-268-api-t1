"""Trace id запроса: свой на каждый запрос, заголовок `X-Trace-ID` в ответе.

Значение — 32 строчных hex-символа, формат trace-id из W3C Trace Context; нулевым
оно не бывает. Сейчас его выдаёт `new_trace_id` из `secrets`. Когда подключим
OpenTelemetry, источником станет trace id текущего спана OTel — формат, заголовок
и поле `trace_id` в теле ошибки при этом не меняются, клиентам и логам перестраиваться
не придётся.

Входящим `X-Trace-ID`, `X-Request-ID` и `traceparent` не доверяем и не принимаем их:
клиент мог бы подставить чужой идентификатор и запутать разбор инцидента. Значение
лежит в `request.state.trace_id` — для тела ошибки и для строки лога о `500`.

Middleware на чистом ASGI, а не `BaseHTTPMiddleware`: тот оборачивает тело
ответа и мешает потоку SSE.
"""

import secrets

from starlette.requests import Request
from starlette.types import ASGIApp, Message, Receive, Scope, Send

HEADER = "X-Trace-ID"
_HEADER_BYTES = HEADER.lower().encode("latin-1")
_ALL_ZEROS = "0" * 32


def new_trace_id() -> str:
    """32 строчных hex-символа (16 случайных байт); нулевой trace-id W3C запрещён."""
    while True:
        trace_id = secrets.token_hex(16)
        if trace_id != _ALL_ZEROS:
            return trace_id


def trace_id_of(request: Request) -> str:
    """Trace id этого запроса; если middleware его не поставил — новый, и он запоминается."""
    trace_id = getattr(request.state, "trace_id", None)
    if not isinstance(trace_id, str):
        trace_id = new_trace_id()
        request.state.trace_id = trace_id
    return trace_id


class TraceIdMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        trace_id = new_trace_id()
        scope.setdefault("state", {})["trace_id"] = trace_id
        value = trace_id.encode("latin-1")

        async def send_with_trace_id(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = [(k, v) for k, v in message.get("headers", []) if k.lower() != _HEADER_BYTES]
                headers.append((_HEADER_BYTES, value))
                message = {**message, "headers": headers}
            await send(message)

        await self.app(scope, receive, send_with_trace_id)
