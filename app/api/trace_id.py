"""Trace id запроса: свой на каждый запрос, заголовок `X-Trace-ID` в ответе.

Значение — 32 строчных hex-символа, формат trace-id из W3C Trace Context. Сейчас его
выдаёт `secrets.token_hex(16)`. Когда подключим
OpenTelemetry, источником станет trace id текущего спана OTel — формат, заголовок
и поле `trace_id` в теле ошибки при этом не меняются, клиентам и логам перестраиваться
не придётся.

Входящим `X-Trace-ID`, `X-Request-ID` и `traceparent` не доверяем и не принимаем их:
клиент мог бы подставить чужой идентификатор и запутать разбор инцидента. Значение
лежит в `request.state.trace_id` — для тела ошибки и для строки лога о `500`. Middleware
пишет его в `scope["state"]`, а этот словарь видят и `OriginMiddleware`, и обработчик
`500` снаружи всех middleware: у них тот же `scope`.

Middleware на чистом ASGI, а не `BaseHTTPMiddleware`: тот оборачивает тело
ответа и мешает потоку SSE.
"""

import secrets

from starlette.types import ASGIApp, Message, Receive, Scope, Send

HEADER = "X-Trace-ID"
_HEADER_BYTES = HEADER.lower().encode("latin-1")


class TraceIdMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        trace_id = secrets.token_hex(16)
        scope.setdefault("state", {})["trace_id"] = trace_id
        value = trace_id.encode("latin-1")

        async def send_with_trace_id(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = [(k, v) for k, v in message.get("headers", []) if k.lower() != _HEADER_BYTES]
                headers.append((_HEADER_BYTES, value))
                message = {**message, "headers": headers}
            await send(message)

        await self.app(scope, receive, send_with_trace_id)
