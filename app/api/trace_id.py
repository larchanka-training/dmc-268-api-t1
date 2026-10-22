"""Trace id запроса: заголовок `X-Trace-ID` в ответе и поле `trace_id` в теле ошибки.

Значение — trace id текущего спана OpenTelemetry, 32 строчных hex-символа (формат
trace-id из W3C Trace Context). Спан запроса открывает встроенная телеметрия FastAPI
снаружи всех middleware, поэтому здесь он уже текущий. Если спана нет (трассировка
выключена `OTEL_SDK_DISABLED`), значение выдаёт `secrets.token_hex(16)` — формат тот же.

Входящим `X-Trace-ID`, `X-Request-ID` и `traceparent` не доверяем и не принимаем их:
клиент мог бы подставить чужой идентификатор и запутать разбор инцидента. Поэтому
каждый HTTP-запрос начинает новый трейс: глобальный пропагатор OpenTelemetry пуст
(`app/infrastructure/telemetry.py`), и FastAPI не берёт родителя из заголовков.
Доверяем только контексту из заголовков AMQP нашей очереди — это сторона воркера.
Валидный `traceparent` клиента становится не родителем, а span link серверного спана
(`link_client_trace`): из трейса API виден переход к трейсу браузера, а trace id
остаётся нашим.

Значение лежит в `request.state.trace_id` — для тела ошибки и для строки лога о `500`.
Middleware пишет его в `scope["state"]`, а этот словарь видят и `OriginMiddleware`, и
обработчик `500` снаружи всех middleware: у них тот же `scope`. Так `trace_id` в теле
`500` совпадает с trace id спана запроса, хотя ответ строится уже вне этого middleware.

Middleware на чистом ASGI, а не `BaseHTTPMiddleware`: тот оборачивает тело
ответа и мешает потоку SSE.
"""

import secrets

from starlette.datastructures import Headers
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.infrastructure.telemetry import current_trace_id, link_client_trace

HEADER = "X-Trace-ID"
_HEADER_BYTES = HEADER.lower().encode("latin-1")


class TraceIdMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        # Спан запроса здесь уже текущий: ссылку получает именно он.
        link_client_trace(Headers(scope=scope).get("traceparent"))
        trace_id = current_trace_id() or secrets.token_hex(16)
        scope.setdefault("state", {})["trace_id"] = trace_id
        value = trace_id.encode("latin-1")

        async def send_with_trace_id(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = [(k, v) for k, v in message.get("headers", []) if k.lower() != _HEADER_BYTES]
                headers.append((_HEADER_BYTES, value))
                message = {**message, "headers": headers}
            await send(message)

        await self.app(scope, receive, send_with_trace_id)
