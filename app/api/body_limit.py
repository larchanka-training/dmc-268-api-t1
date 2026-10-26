"""Лимит тела запроса: тело больше лимита не читается целиком (#72).

Лимита два: `WEBHOOK_BODY_MAX_BYTES` (25 МиБ, потолок доставки GitHub) — для путей приёма
вебхука, `REQUEST_BODY_MAX_BYTES` (1 МиБ) — для остальных. Путь выбирается по префиксу до
роутинга: путь, который только начинается как вебхук, роутер всё равно не найдёт.

Приём вебхука открыт без сессии и читает сырое тело до проверки подписи — без лимита
любой мог бы занять память процесса многогигабайтным телом. Ручки с JSON-телом читают
его до проверки сессии. Снаружи тело режет nginx фронта (`client_max_body_size`), здесь —
второй эшелон для запроса в обход него. Лимит стоит до роутинга, разбора тела, сессии и подписи:

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

# Приём вебхука (`app/api/webhooks.py`): только ему нужен лимит доставки GitHub.
WEBHOOK_PREFIX = "/api/v1/webhooks/"


class RequestBodyLimitMiddleware:
    def __init__(self, app: ASGIApp, max_bytes: int, webhook_max_bytes: int) -> None:
        self.app = app
        self.max_bytes = max_bytes
        self.webhook_max_bytes = webhook_max_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        limit = self.webhook_max_bytes if scope["path"].startswith(WEBHOOK_PREFIX) else self.max_bytes
        headers = Headers(scope=scope)
        declared = _declared_length(headers)
        if declared is not None and declared > limit:
            await _reject(scope, receive, send, limit)
            return
        if not declared and "transfer-encoding" not in headers:
            # Ни длины, ни chunked: по HTTP/1.1 (RFC 9112, 6.3) тела у запроса нет. GET и
            # поток SSE идут мимо как есть: ждать тела, которого нет, здесь незачем.
            await self.app(scope, receive, send)
            return
        chunks: list[bytes] = []
        size = 0
        pending: list[Message] = []
        while True:
            message = await receive()
            if message["type"] != "http.request":
                # Клиент ушёл, не дослав тело: приложение увидит это само, как без лимита.
                pending.append(message)
                break
            size += len(message.get("body", b""))
            if size > limit:
                await _reject(scope, receive, send, limit)
                return
            chunks.append(message.get("body", b""))
            if not message.get("more_body", False):
                # Тело — одна копия: склейка уходит в `pending`, куски отпускаются сразу.
                # Ссылку на тело в кадре держать нельзя — оно жило бы, пока работает ручка.
                pending.append({"type": "http.request", "body": b"".join(chunks), "more_body": False})
                chunks.clear()
                break

        async def replay() -> Message:
            # Тело — одним сообщением, дальше — сервер как есть (`http.disconnect` для SSE).
            if pending:
                return pending.pop()
            return await receive()

        await self.app(scope, replay, send)


async def _reject(scope: Scope, receive: Receive, send: Send, limit: int) -> None:
    response = problem_response(
        Request(scope),
        HTTPStatus.CONTENT_TOO_LARGE,
        ErrorCode.REQUEST_BODY_TOO_LARGE,
        f"Тело запроса больше лимита {limit} байт.",
        headers={"Connection": "close"},
    )
    await response(scope, receive, send)


def _declared_length(headers: Headers) -> int | None:
    """`Content-Length` запроса или `None`, если его нет или он не из цифр ASCII.

    `str.isdigit()` без `isascii()` принимает «²» (заголовок декодирован как latin-1), и `int()`
    на нём упал бы — `500`. Нечисловой `Content-Length` без `Transfer-Encoding` идёт мимо
    подсчёта, как запрос без тела: такой заголовок отклоняет сервер (h11 — `400`) до приложения.
    """
    value = headers.get("content-length")
    if value is None or not (value.isascii() and value.isdigit()):
        return None
    return int(value)
