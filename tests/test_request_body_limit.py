"""Лимит тела запроса (#72): тело больше лимита отклоняется до чтения целиком и до подписи.

Объявленная длина проверяется до чтения тела, поток без `Content-Length` считается по
байтам и обрывается на лимите. Ответ — `413 REQUEST_BODY_TOO_LARGE` в формате Problem
Details с `trace_id`. Отказ по длине и по потоку проверяется на уровне ASGI: тест видит,
сколько тела приложение попросило у сервера. Лимиты — значения по умолчанию: тест держит
и то, что вебхук принимает максимум доставки GitHub, а остальные ручки — только 1 МиБ.
"""

import asyncio
import hashlib
import hmac
import json
import tracemalloc
from collections.abc import Iterator, MutableMapping
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.datastructures import Headers

from app.api import webhooks
from app.api.body_limit import _declared_length
from app.api.factory import create_app
from app.config import Settings, load_settings

from .test_problem_details import ORIGIN, assert_problem

SECRET = "test-secret"
SETTINGS = Settings(
    database_url="postgresql+psycopg://test:test@localhost/test",
    rabbitmq_url="amqp://guest:guest@localhost//",
    github_webhook_secret=SECRET,
    public_origin=ORIGIN,
)
# Умолчания: вебхук — 25 МиБ, не меньше потолка доставки GitHub («Payloads are capped at
# 25 MB»); остальные ручки — 1 МиБ.
LIMIT = 25 * 1024 * 1024
BODY_LIMIT = 1024 * 1024
CHUNK = 1024 * 1024
WEBHOOK = "/api/v1/webhooks/github"


def sign(body: bytes) -> str:
    return "sha256=" + hmac.new(SECRET.encode(), body, hashlib.sha256).hexdigest()


@pytest.fixture
def app() -> FastAPI:
    return create_app(SETTINGS)


@pytest.fixture
def signature_never_checked(monkeypatch: pytest.MonkeyPatch) -> None:
    def fail(*_: Any) -> bool:
        raise AssertionError("подпись проверяется до лимита тела")

    monkeypatch.setattr(webhooks, "verify_hmac", fail)


class Wire:
    """Сервер на стороне ASGI: отдаёт тело кусками и считает, сколько у него попросили."""

    def __init__(self, chunks: Iterator[bytes]) -> None:
        self._chunks = chunks
        self.received = 0
        self.messages: list[dict[str, Any]] = []

    async def receive(self) -> dict[str, Any]:
        chunk = next(self._chunks, None)
        if chunk is None:
            return {"type": "http.request", "body": b"", "more_body": False}
        self.received += len(chunk)
        return {"type": "http.request", "body": chunk, "more_body": True}

    async def send(self, message: MutableMapping[str, Any]) -> None:
        self.messages.append(dict(message))

    @property
    def status(self) -> int:
        (start,) = [m for m in self.messages if m["type"] == "http.response.start"]
        return int(start["status"])

    @property
    def headers(self) -> dict[str, str]:
        (start,) = [m for m in self.messages if m["type"] == "http.response.start"]
        return {k.decode().lower(): v.decode() for k, v in start["headers"]}

    @property
    def body(self) -> dict[str, Any]:
        payload: dict[str, Any] = json.loads(
            b"".join(m.get("body", b"") for m in self.messages if m["type"] == "http.response.body")
        )
        return payload


def call(app: FastAPI, wire: Wire, headers: dict[str, str], path: str = WEBHOOK) -> None:
    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": path,
        "raw_path": path.encode(),
        "query_string": b"",
        "root_path": "",
        "headers": [(b"host", b"testserver"), *((k.lower().encode(), v.encode()) for k, v in headers.items())],
        "client": ("127.0.0.1", 50000),
        "server": ("testserver", 80),
    }
    asyncio.run(app(scope, wire.receive, wire.send))


def stream(total: int, chunk: int = CHUNK) -> Iterator[bytes]:
    sent = 0
    while sent < total:
        size = min(chunk, total - sent)
        sent += size
        yield b"x" * size


def assert_too_large(wire: Wire) -> None:
    assert wire.status == 413
    assert wire.headers["content-type"] == "application/problem+json"
    body = wire.body
    assert body["code"] == "REQUEST_BODY_TOO_LARGE"
    assert body["status"] == 413
    assert wire.headers["x-trace-id"] == body["trace_id"]
    # Тело не дочитано: соединение не переиспользуется под следующий запрос.
    assert wire.headers["connection"] == "close"


def test_declared_length_over_the_limit_is_rejected_without_reading_the_body(
    app: FastAPI, signature_never_checked: None
) -> None:
    def never() -> Iterator[bytes]:
        raise AssertionError("тело читается, хотя Content-Length больше лимита")
        yield b""

    wire = Wire(never())

    call(app, wire, {"content-length": str(LIMIT + 1), "x-hub-signature-256": "sha256=00"})

    assert_too_large(wire)
    assert wire.received == 0


def test_stream_without_length_over_the_limit_stops_at_the_limit(
    app: FastAPI, signature_never_checked: None
) -> None:
    """Chunked без `Content-Length`, подпись верна: отказ — по лимиту, подпись не проверяется."""
    total = LIMIT + 10 * CHUNK
    signature = sign(b"x" * total)
    wire = Wire(stream(total))

    call(app, wire, {"transfer-encoding": "chunked", "x-hub-signature-256": signature})

    assert_too_large(wire)
    # Дочитан ровно кусок, на котором перешли лимит, — дальше сервер ничего не отдавал.
    assert wire.received == LIMIT + CHUNK


def test_stream_longer_than_its_declared_length_is_cut_at_the_limit(
    app: FastAPI, signature_never_checked: None
) -> None:
    wire = Wire(stream(LIMIT + 10 * CHUNK))

    call(app, wire, {"content-length": "10", "x-hub-signature-256": "sha256=00"})

    assert_too_large(wire)
    assert wire.received == LIMIT + CHUNK


@pytest.mark.parametrize("chunked", [False, True], ids=["content-length", "chunked"])
def test_signed_body_of_exactly_the_limit_is_accepted_as_before(app: FastAPI, chunked: bool) -> None:
    """Тело на лимите — обычная доставка: подпись сошлась, тело не JSON — 202 ignored."""
    body = b"x" * LIMIT
    headers = {"x-hub-signature-256": sign(body), "x-github-event": "pull_request"}
    headers |= {"transfer-encoding": "chunked"} if chunked else {"content-length": str(LIMIT)}
    wire = Wire(stream(LIMIT))

    call(app, wire, headers)

    assert wire.status == 202
    assert wire.body == {"status": "ignored"}


def test_handler_holds_one_copy_of_the_body(app: FastAPI, monkeypatch: pytest.MonkeyPatch) -> None:
    """Пока работает ручка, тело лежит в памяти одной копией: middleware ссылок не держит.

    Память меряется в `verify_hmac`, когда тело уже прочитано: дочитанные куски и склейка в
    кадре middleware дали бы две-три копии, и параллельных доставок до OOM влезло бы втрое меньше.
    """
    body = b"x" * LIMIT
    signature = sign(body)
    held: list[int] = []

    def measure(*_: Any) -> bool:
        held.append(tracemalloc.get_traced_memory()[0])
        return False

    monkeypatch.setattr(webhooks, "verify_hmac", measure)
    wire = Wire(stream(LIMIT, chunk=64 * 1024))
    tracemalloc.start()
    try:
        call(app, wire, {"content-length": str(LIMIT), "x-hub-signature-256": signature})
    finally:
        tracemalloc.stop()

    assert wire.status == 401
    (current,) = held
    assert current <= 1.2 * LIMIT, f"{current / LIMIT:.2f} тела в памяти"


def test_wrong_signature_within_the_limit_is_still_unauthorized(app: FastAPI) -> None:
    wire = Wire(stream(1024))

    call(app, wire, {"content-length": "1024", "x-hub-signature-256": "sha256=00"})

    assert wire.status == 401
    assert wire.body["code"] == "WEBHOOK_SIGNATURE_INVALID"


def test_body_route_rejects_an_oversize_stream_before_the_session(app: FastAPI) -> None:
    """`POST /reviews` без сессии: тело больше лимита — 413, а не 401 после чтения целиком."""
    wire = Wire(stream(BODY_LIMIT + 10 * CHUNK))

    call(
        app,
        wire,
        {"transfer-encoding": "chunked", "content-type": "application/json", "origin": ORIGIN},
        path="/api/v1/reviews",
    )

    assert_too_large(wire)
    assert wire.received == BODY_LIMIT + CHUNK


def test_webhook_limit_does_not_apply_to_other_routes(app: FastAPI) -> None:
    """Тело, которое вебхук принимает, ручке с JSON-телом не достаётся: у неё свой 1 МиБ.

    Иначе 25 МиБ на каждую ручку — и двадцать запросов без сессии занимают всю память API.
    """
    size = BODY_LIMIT + 1
    headers = {"content-length": str(size), "content-type": "application/json", "origin": ORIGIN}
    route = Wire(stream(size))

    call(app, route, headers, path="/api/v1/reviews")

    assert_too_large(route)
    assert route.received == 0
    assert str(BODY_LIMIT) in route.body["detail"]

    body = b"x" * size
    webhook = Wire(stream(size))
    call(app, webhook, {"content-length": str(size), "x-hub-signature-256": sign(body), "x-github-event": "pull_request"})
    assert webhook.status == 202


def test_webhook_limit_is_chosen_by_path_prefix_only(app: FastAPI) -> None:
    """Путь, похожий на вебхук, но без его префикса, получает общий лимит."""
    wire = Wire(stream(BODY_LIMIT + 1))

    call(app, wire, {"content-length": str(BODY_LIMIT + 1), "origin": ORIGIN}, path="/api/v1/webhooks")

    assert_too_large(wire)


def test_oversize_body_is_a_problem_with_trace_id_over_http(app: FastAPI) -> None:
    client = TestClient(app, base_url=ORIGIN)

    response = client.post(WEBHOOK, content=b"x" * (LIMIT + 1), headers={"X-Hub-Signature-256": "sha256=00"})

    body = assert_problem(response, 413, "REQUEST_BODY_TOO_LARGE", WEBHOOK)
    assert str(LIMIT) in body["detail"]


def test_default_limits() -> None:
    assert SETTINGS.webhook_body_max_bytes == LIMIT >= 25_000_000
    assert SETTINGS.request_body_max_bytes == BODY_LIMIT


def test_limit_is_read_from_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", SETTINGS.database_url)
    monkeypatch.setenv("RABBITMQ_URL", SETTINGS.rabbitmq_url)
    monkeypatch.setenv("GITHUB_WEBHOOK_SECRET", SECRET)
    monkeypatch.setenv("REQUEST_BODY_MAX_BYTES", "1024")
    monkeypatch.setenv("WEBHOOK_BODY_MAX_BYTES", "2048")
    settings = load_settings()
    assert (settings.request_body_max_bytes, settings.webhook_body_max_bytes) == (1024, 2048)


def test_every_operation_with_a_body_declares_413() -> None:
    """Контракт: у ручек с телом (и у вебхука, читающего сырое тело) `413` — своей моделью."""
    document = create_app(SETTINGS).openapi()
    with_body = {
        f"{method.upper()} {path}"
        for path, item in document["paths"].items()
        for method, operation in item.items()
        if "requestBody" in operation or path.startswith("/api/v1/webhooks/")
    }
    assert "POST /api/v1/webhooks/{provider}" in with_body and "POST /api/v1/reviews" in with_body
    for name in with_body:
        method, path = name.split(" ", 1)
        content = document["paths"][path][method.lower()]["responses"]["413"]["content"]
        assert content["application/problem+json"]["schema"] == {
            "$ref": "#/components/schemas/RequestBodyTooLargeProblem"
        }, name


@pytest.mark.parametrize("value", ["²", "1²", "abc", "-1", ""])
def test_non_ascii_digit_content_length_is_not_a_length(value: str) -> None:
    """Заголовок декодируется как latin-1: `str.isdigit()` принимает «²», а `int()` на нём падает."""
    headers = Headers(raw=[(b"content-length", value.encode("latin-1"))])
    assert _declared_length(headers) is None
