"""Формат ошибок — RFC 9457 Problem Details: построитель, обработчики и `X-Trace-ID`.

Без базы: ручки, которым она нужна, получают фейковый unit of work, а для `500` и
`HTTPException` к приложению добавляются роуты, которые падают.
"""

import json
import logging
import re
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from pydantic import ValidationError
from starlette.requests import Request

from app.api.dependencies import get_unit_of_work_factory
from app.api.errors import problem_response
from app.api.factory import create_app
from app.api.problems import PROBLEM_MODELS, AnyProblem, ErrorCode, problem_type
from app.config import Settings

from .fakes.auth import sign_in
from .fakes.unit_of_work import FakeUnitOfWork

ORIGIN = "https://review.example"
SETTINGS = Settings(
    database_url="postgresql+psycopg://test:test@localhost/test",
    rabbitmq_url="amqp://guest:guest@localhost//",
    github_webhook_secret="test-secret",
    public_origin=ORIGIN,
)
PROBLEM_FIELDS = {"type", "title", "status", "detail", "instance", "code", "trace_id"}
SECRET_TEXT = "секрет-из-исключения"
TRACE_ID = re.compile(r"^[0-9a-f]{32}$")
OPENAPI_JSON = Path(__file__).resolve().parent.parent / "docs" / "openapi" / "openapi.json"
REVIEW_BODY = {"provider": "github", "provider_repository_id": "1", "pull_request_number": 1, "trigger": "manual"}
RUN_ID = "0192f0c4-7a1e-7b3c-9d2e-4f5a6b7c8d9e"
CONNECT_BODY = {"provider": "github", "provider_id": "123456789"}
JSON = {"content-type": "application/json"}


def build_app() -> FastAPI:
    app = create_app(SETTINGS)
    uow = FakeUnitOfWork()
    app.dependency_overrides[get_unit_of_work_factory] = lambda: lambda: uow

    @app.get("/api/v1/_boom")
    def boom() -> None:
        raise RuntimeError(SECRET_TEXT)

    @app.get("/api/v1/_throttled")
    def throttled() -> None:
        raise HTTPException(429, detail=SECRET_TEXT, headers={"Retry-After": "5"})

    @app.get("/api/v1/_not_modified")
    def not_modified() -> None:
        raise HTTPException(304)

    return app


@pytest.fixture
def client() -> TestClient:
    return TestClient(build_app(), base_url=ORIGIN, raise_server_exceptions=False)


def assert_problem(response, status: int, code: str, path: str) -> dict[str, Any]:
    """Общая часть любой ошибки: тип содержимого, поля, `X-Trace-ID` = `trace_id`."""
    assert response.status_code == status
    assert response.headers["content-type"] == "application/problem+json"
    body: dict[str, Any] = response.json()
    assert PROBLEM_FIELDS <= body.keys()
    assert body["type"] == problem_type(code)
    assert body["type"].startswith("urn:dmc-268:problem:")
    assert body["title"] == PROBLEM_MODELS[ErrorCode(code)].model_fields["title"].default
    assert body["status"] == status
    assert body["code"] == code
    assert body["instance"] == path
    assert TRACE_ID.fullmatch(body["trace_id"])
    assert body["trace_id"] != "0" * 32
    assert response.headers["X-Trace-ID"] == body["trace_id"]
    return body


def test_problem_type_is_kebab_case_urn() -> None:
    assert problem_type("VALIDATION_ERROR") == "urn:dmc-268:problem:validation-error"


def _request() -> Request:
    request = Request(
        {"type": "http", "method": "GET", "path": "/x", "raw_path": b"/x", "query_string": b"a=1", "headers": []}
    )
    request.state.trace_id = "4bf92f3577b34da6a3ce929d0e0e4736"
    return request


def test_builder_puts_extensions_and_headers() -> None:
    errors = [{"where": "query.limit", "reason": "ожидается целое число"}]
    response = problem_response(
        _request(), 422, ErrorCode.VALIDATION_ERROR, "подробности", headers={"Allow": "GET"}, errors=errors
    )
    assert response.media_type == "application/problem+json"
    assert response.headers["X-Trace-ID"] == "4bf92f3577b34da6a3ce929d0e0e4736"
    assert response.headers["Allow"] == "GET"
    body = json.loads(bytes(response.body))
    assert body["instance"] == "/x"
    assert body["trace_id"] == "4bf92f3577b34da6a3ce929d0e0e4736"
    assert body["title"] == "Запрос не прошёл проверку"
    assert body["errors"] == errors


def test_builder_rejects_a_status_the_code_does_not_allow() -> None:
    """Тело строится через модель кода: `REVIEW_NOT_FOUND` бывает только `404`."""
    with pytest.raises(ValidationError):
        problem_response(_request(), 409, ErrorCode.REVIEW_NOT_FOUND, "подробности")


def test_validation_error_lists_errors_without_echo(client: TestClient) -> None:
    # Ручка без сессии: у ручек с сессией `401` наступает раньше проверки параметров.
    response = client.post("/api/v1/webhooks/bitbucket-секрет", params={"q": "секрет"})
    # `instance` — путь как пришёл, закодированный; эхо ввода запрещено в `detail` и `errors`.
    encoded = "/api/v1/webhooks/bitbucket-%D1%81%D0%B5%D0%BA%D1%80%D0%B5%D1%82"
    body = assert_problem(response, 422, "VALIDATION_ERROR", encoded)
    assert body["errors"] == [{"where": "path.provider", "reason": "значение не из допустимого списка"}]
    assert "секрет" not in body["detail"]
    assert "секрет" not in str(body["errors"])


def test_unknown_path_is_route_not_found(client: TestClient) -> None:
    response = client.get("/api/v1/nope", params={"q": "1"})
    body = assert_problem(response, 404, "ROUTE_NOT_FOUND", "/api/v1/nope")
    assert "errors" not in body


def test_unknown_path_outside_api_is_problem_too(client: TestClient) -> None:
    assert_problem(client.get("/nope"), 404, "ROUTE_NOT_FOUND", "/nope")


def test_instance_is_the_encoded_path_without_query(client: TestClient) -> None:
    """`instance` — URI-reference: путь как пришёл, а не раскодированный `request.url.path`."""
    response = client.get("/api/v1/a%20b", params={"q": "1"})
    assert_problem(response, 404, "ROUTE_NOT_FOUND", "/api/v1/a%20b")


def test_other_http_exception_keeps_status_and_headers(client: TestClient) -> None:
    response = client.get("/api/v1/_throttled")
    body = assert_problem(response, 429, "HTTP_ERROR", "/api/v1/_throttled")
    assert response.headers["Retry-After"] == "5"
    assert body["detail"] == "Запрос отклонён со статусом 429."
    assert SECRET_TEXT not in response.text


def test_http_exception_outside_4xx_5xx_is_internal_error(client: TestClient) -> None:
    assert_problem(client.get("/api/v1/_not_modified"), 500, "INTERNAL_ERROR", "/api/v1/_not_modified")


def test_server_serves_the_exported_contract() -> None:
    """`/openapi.json` — та же схема, что экспорт `docs/openapi/openapi.json`; `/docs` — Swagger UI по ней."""
    # Без тестовых роутов `build_app`: они попали бы в схему.
    client = TestClient(create_app(SETTINGS), base_url=ORIGIN)
    response = client.get("/openapi.json")
    assert response.status_code == 200
    assert response.json() == json.loads(OPENAPI_JSON.read_text(encoding="utf-8"))
    assert "AnyProblem" in response.json()["components"]["schemas"]

    docs = client.get("/docs")
    assert docs.status_code == 200
    assert docs.headers["content-type"].startswith("text/html")
    assert "/openapi.json" in docs.text


def test_redoc_is_not_served(client: TestClient) -> None:
    assert_problem(client.get("/redoc"), 404, "ROUTE_NOT_FOUND", "/redoc")


def test_wrong_method_is_method_not_allowed_with_allow(client: TestClient) -> None:
    response = client.delete("/api/v1/webhooks/github")
    assert_problem(response, 405, "METHOD_NOT_ALLOWED", "/api/v1/webhooks/github")
    assert response.headers["Allow"] == "POST"


def test_unhandled_exception_is_internal_error_without_details(client: TestClient) -> None:
    response = client.get("/api/v1/_boom")
    body = assert_problem(response, 500, "INTERNAL_ERROR", "/api/v1/_boom")
    assert SECRET_TEXT not in response.text
    assert "RuntimeError" not in response.text
    assert "Traceback" not in response.text
    assert "trace_id" in body["detail"]


def test_unhandled_exception_writes_one_log_line_with_trace_id(
    client: TestClient, caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    # `fileConfig` в тестах Alembic отключает уже созданные логгеры.
    # TODO: убрать после мёржа #65 (change fix-migration-logging), как и такой же
    # обход в tests/test_auth_api.py.
    monkeypatch.setattr(logging.getLogger("app.api.errors"), "disabled", False)
    caplog.set_level(logging.ERROR, logger="app.api.errors")

    response = client.get("/api/v1/_boom", params={"q": SECRET_TEXT})

    trace_id = response.json()["trace_id"]
    records = [r for r in caplog.records if r.name == "app.api.errors"]
    assert len(records) == 1
    record = records[0]
    assert record.levelno == logging.ERROR
    assert record.getMessage() == f"Необработанное исключение: trace_id={trace_id} GET /api/v1/_boom"
    assert record.exc_info is None
    assert SECRET_TEXT not in record.getMessage()


def test_unhandled_exception_is_not_swallowed() -> None:
    """Ответ отдан, а исключение уходит дальше — сервер пишет его в лог со стектрейсом."""
    with pytest.raises(RuntimeError, match=SECRET_TEXT):
        TestClient(build_app(), base_url=ORIGIN).get("/api/v1/_boom")


def test_session_401_clears_cookie(client: TestClient) -> None:
    client.cookies.set("session", "stale", domain="review.example")
    response = client.get("/api/v1/auth/me")
    assert_problem(response, 401, "UNAUTHENTICATED", "/api/v1/auth/me")
    set_cookie = response.headers.get("set-cookie", "")
    assert set_cookie.startswith('session=""') and "Max-Age=0" in set_cookie


def test_session_401_clears_empty_cookie(client: TestClient) -> None:
    response = client.get("/api/v1/auth/me", headers={"Cookie": "session="})
    assert_problem(response, 401, "UNAUTHENTICATED", "/api/v1/auth/me")
    set_cookie = response.headers.get("set-cookie", "")
    assert set_cookie.startswith('session=""') and "Max-Age=0" in set_cookie


def test_session_401_without_cookie_sets_no_cookie(client: TestClient) -> None:
    response = client.get("/api/v1/repositories")
    assert_problem(response, 401, "UNAUTHENTICATED", "/api/v1/repositories")
    assert "set-cookie" not in response.headers


def test_incoming_trace_headers_are_ignored(client: TestClient) -> None:
    chosen = "4bf92f3577b34da6a3ce929d0e0e4736"
    response = client.get(
        "/api/v1/nope",
        headers={
            "X-Trace-ID": chosen,
            "X-Request-ID": chosen,
            "traceparent": f"00-{chosen}-00f067aa0ba902b7-01",
        },
    )
    body = assert_problem(response, 404, "ROUTE_NOT_FOUND", "/api/v1/nope")
    assert body["trace_id"] != chosen
    assert response.headers.get_list("X-Trace-ID") == [body["trace_id"]]
    assert "X-Request-ID" not in response.headers


def test_every_response_carries_a_fresh_trace_id(client: TestClient) -> None:
    first = client.get("/health")
    second = client.get("/health")
    assert TRACE_ID.fullmatch(first.headers["X-Trace-ID"])
    assert first.headers["X-Trace-ID"] != second.headers["X-Trace-ID"]


def _signed_in_client() -> TestClient:
    app = build_app()
    sign_in(app)
    return TestClient(app, base_url=ORIGIN)


def test_planned_endpoint_answers_not_implemented() -> None:
    """`POST /reviews` описан в схеме полностью, а отвечает `501 NOT_IMPLEMENTED`."""
    response = _signed_in_client().post("/api/v1/reviews", json=REVIEW_BODY, headers={"Origin": ORIGIN})
    assert_problem(response, 501, "NOT_IMPLEMENTED", "/api/v1/reviews")


def test_planned_endpoint_checks_origin_session_and_body_first(client: TestClient) -> None:
    """Заглушка проверяет `Origin`, сессию и тело, как изменяющие ручки."""
    assert_problem(client.post("/api/v1/reviews", json=REVIEW_BODY), 403, "ORIGIN_FORBIDDEN", "/api/v1/reviews")
    response = client.post("/api/v1/reviews", json=REVIEW_BODY, headers={"Origin": ORIGIN})
    assert_problem(response, 401, "UNAUTHENTICATED", "/api/v1/reviews")
    response = _signed_in_client().post(
        "/api/v1/reviews", json={**REVIEW_BODY, "pull_request_number": 0}, headers={"Origin": ORIGIN}
    )
    problem = assert_problem(response, 422, "VALIDATION_ERROR", "/api/v1/reviews")
    assert problem["errors"] == [{"where": "body.pull_request_number", "reason": "должно быть не меньше 1"}]


def test_repository_connect_checks_session_origin_and_body_first(client: TestClient) -> None:
    """Подключение проверяет `Origin`, сессию и тело раньше похода к GitHub."""
    response = client.get("/api/v1/repositories/available")
    assert_problem(response, 401, "UNAUTHENTICATED", "/api/v1/repositories/available")
    response = client.post("/api/v1/repositories", json=CONNECT_BODY)
    assert_problem(response, 403, "ORIGIN_FORBIDDEN", "/api/v1/repositories")
    response = client.post("/api/v1/repositories", json=CONNECT_BODY, headers={"Origin": ORIGIN})
    assert_problem(response, 401, "UNAUTHENTICATED", "/api/v1/repositories")
    response = _signed_in_client().post(
        "/api/v1/repositories", json={**CONNECT_BODY, "provider_id": ""}, headers={"Origin": ORIGIN}
    )
    problem = assert_problem(response, 422, "VALIDATION_ERROR", "/api/v1/repositories")
    assert problem["errors"] == [{"where": "body.provider_id", "reason": "длина строки должна быть не меньше 1"}]


@pytest.mark.parametrize("part", ["diff", "steps"])
def test_planned_review_run_parts_answer_not_implemented(part: str) -> None:
    """Дифф и шаги прогона описаны в схеме, а отвечают `501`."""
    path = f"/api/v1/reviews/{RUN_ID}/{part}"
    assert_problem(_signed_in_client().get(path), 501, "NOT_IMPLEMENTED", path)


@pytest.mark.parametrize("part", ["diff", "steps"])
def test_planned_review_run_parts_check_session_and_id_first(client: TestClient, part: str) -> None:
    """Заглушки проверяют сессию и id прогона, как реализованные ручки."""
    path = f"/api/v1/reviews/{RUN_ID}/{part}"
    assert_problem(client.get(path), 401, "UNAUTHENTICATED", path)
    path = f"/api/v1/reviews/not-a-uuid/{part}"
    assert_problem(_signed_in_client().get(path), 422, "VALIDATION_ERROR", path)


def test_origin_is_checked_before_the_body_is_parsed(client: TestClient) -> None:
    """Битый JSON без `Origin` — `403`, а не `422`: `Origin` проверяет middleware до разбора тела."""
    response = client.post("/api/v1/reviews", content=b"{bad", headers=JSON)
    assert_problem(response, 403, "ORIGIN_FORBIDDEN", "/api/v1/reviews")
    response = client.post("/api/v1/reviews", content=b"{bad", headers={**JSON, "Origin": "https://evil.example"})
    assert_problem(response, 403, "ORIGIN_FORBIDDEN", "/api/v1/reviews")


@pytest.mark.parametrize("method", ["POST", "PUT", "PATCH", "DELETE"])
def test_origin_is_checked_before_routing(client: TestClient, method: str) -> None:
    """Изменяющий запрос под /api/v1 без `Origin` получает `403` даже на неизвестный путь."""
    assert_problem(client.request(method, "/api/v1/nope"), 403, "ORIGIN_FORBIDDEN", "/api/v1/nope")
    # Безопасные методы и пути вне /api/v1 проверка не трогает.
    assert_problem(client.get("/api/v1/nope"), 404, "ROUTE_NOT_FOUND", "/api/v1/nope")
    assert_problem(client.post("/nope"), 404, "ROUTE_NOT_FOUND", "/nope")


def test_empty_public_origin_closes_unsafe_requests() -> None:
    """Пустой `public_origin` не совпадает и с пустым `Origin`: изменяющие запросы закрыты."""
    client = TestClient(create_app(SETTINGS.model_copy(update={"public_origin": ""})), base_url=ORIGIN)
    response = client.post("/api/v1/auth/logout", headers={"Origin": ""})
    assert_problem(response, 403, "ORIGIN_FORBIDDEN", "/api/v1/auth/logout")


def test_with_origin_a_malformed_body_without_session_is_422(client: TestClient) -> None:
    """Порядок после `Origin`: FastAPI разбирает JSON раньше зависимостей, поэтому битое тело
    без сессии — `422`; разобранное тело без сессии — уже `401` (проверка выше)."""
    response = client.post("/api/v1/reviews", content=b"{bad", headers={**JSON, "Origin": ORIGIN})
    problem = assert_problem(response, 422, "VALIDATION_ERROR", "/api/v1/reviews")
    assert problem["errors"][0]["reason"] == "тело запроса — не JSON"


def test_logout_checks_origin_in_middleware(client: TestClient) -> None:
    response = client.post("/api/v1/auth/logout")
    assert_problem(response, 403, "ORIGIN_FORBIDDEN", "/api/v1/auth/logout")
    assert "set-cookie" not in response.headers
    assert client.post("/api/v1/auth/logout", headers={"Origin": ORIGIN}).status_code == 204


def test_extra_field_name_is_not_echoed() -> None:
    key = "<img src=x onerror=alert(1)>"
    response = _signed_in_client().post("/api/v1/reviews", json={**REVIEW_BODY, key: 1}, headers={"Origin": ORIGIN})
    problem = assert_problem(response, 422, "VALIDATION_ERROR", "/api/v1/reviews")
    assert problem["errors"] == [{"where": "body", "reason": "лишнее поле"}]
    assert "onerror" not in response.text


def test_invalid_json_where_has_no_offset() -> None:
    response = _signed_in_client().post(
        "/api/v1/reviews",
        content=b'{"x": ',
        headers={"Origin": ORIGIN, "Content-Type": "application/json"},
    )
    problem = assert_problem(response, 422, "VALIDATION_ERROR", "/api/v1/reviews")
    assert problem["errors"] == [{"where": "body", "reason": "тело запроса — не JSON"}]


@pytest.mark.parametrize(
    ("change", "error"),
    [
        ({"pull_request_number": "5"}, {"where": "body.pull_request_number", "reason": "ожидается целое число"}),
        ({"pull_request_number": True}, {"where": "body.pull_request_number", "reason": "ожидается целое число"}),
        ({"provider_repository_id": 1}, {"where": "body.provider_repository_id", "reason": "ожидается строка"}),
        (
            {"provider_repository_id": ""},
            {"where": "body.provider_repository_id", "reason": "длина строки должна быть не меньше 1"},
        ),
        (
            {"provider_repository_id": "x" * 256},
            {"where": "body.provider_repository_id", "reason": "длина строки должна быть не больше 255"},
        ),
        (
            {"pull_request_number": 2**31},
            {"where": "body.pull_request_number", "reason": "должно быть не больше 2147483647"},
        ),
        ({"provider": "bitbucket"}, {"where": "body.provider", "reason": "значение не из допустимого списка"}),
    ],
)
def test_review_request_body_is_strict(change: dict[str, Any], error: dict[str, str]) -> None:
    """Тело без приведения типов и в границах колонок; enum — строкой из списка."""
    response = _signed_in_client().post("/api/v1/reviews", json={**REVIEW_BODY, **change}, headers={"Origin": ORIGIN})
    problem = assert_problem(response, 422, "VALIDATION_ERROR", "/api/v1/reviews")
    assert problem["errors"] == [error]


def test_review_request_body_is_snake_case_only() -> None:
    """Тело — в snake_case, как ответы: camelCase-ключ — лишнее поле, а не синоним."""
    body = {"provider": "github", "providerRepositoryId": "1", "pullRequestNumber": 1, "trigger": "manual"}
    response = _signed_in_client().post("/api/v1/reviews", json=body, headers={"Origin": ORIGIN})
    problem = assert_problem(response, 422, "VALIDATION_ERROR", "/api/v1/reviews")
    assert {"where": "body.provider_repository_id", "reason": "обязательный параметр не передан"} in problem["errors"]
    assert {"where": "body", "reason": "лишнее поле"} in problem["errors"]


@pytest.mark.parametrize(
    "path", ["/api/v1/repositories", "/api/v1/repositories/00000000-0000-0000-0000-000000000001/pull-requests"]
)
@pytest.mark.parametrize(
    ("offset", "reason"),
    [
        (str(2**63), "должно быть не больше 9223372036854775807"),
        ("1" + "0" * 4300, "число слишком большое"),
    ],
)
def test_offset_beyond_bigint_is_422(path: str, offset: str, reason: str) -> None:
    """`OFFSET` в PostgreSQL — bigint: больше база не примет, поэтому `422`, а не `500`."""
    response = _signed_in_client().get(path, params={"offset": offset})
    problem = assert_problem(response, 422, "VALIDATION_ERROR", path)
    assert problem["errors"] == [{"where": "query.offset", "reason": reason}]
    assert offset not in response.text


def test_server_bodies_match_their_models(client: TestClient) -> None:
    """Тела, которые сервер отдаёт на деле, проходят модель своего кода (`AnyProblem`).

    Каждый код из `ErrorCode` здесь представлен хотя бы одним настоящим ответом — кроме
    `REVIEW_ALREADY_ACTIVE`: его отдаст ручной запуск, а он пока отвечает `501`.
    """
    from app.domain.vcs_errors import VcsError

    from .api.test_webhooks import make_client, post_webhook
    from .fakes import FakeUow, FakeVcs
    from .fakes.auth import SIGNED_IN
    from .fakes.installation_catalog import FakeInstallationCatalog
    from .test_pull_requests_api import _client as pull_requests_client
    from .test_repositories_api import API
    from .test_repositories_api import _client as repositories_client
    from .test_repositories_api import _connect as connect_repository
    from .test_repositories_api import _repository as registered_repository
    from .test_reviews_api import _client as reviews_client

    signed_in = repositories_client(FakeUnitOfWork())
    connected = FakeUnitOfWork()
    connected.repositories.add(registered_repository(101))
    scm_down, _ = make_client(uow=FakeUow(), vcs=FakeVcs(error=VcsError("HTTP 502")))
    responses = [
        client.post("/api/v1/webhooks/bitbucket"),
        signed_in.get("/api/v1/repositories", params={"limit": "abc"}),
        reviews_client(FakeUnitOfWork()).get("/api/v1/reviews/00000000-0000-0000-0000-000000000001"),
        pull_requests_client(FakeUnitOfWork()).get(
            "/api/v1/repositories/00000000-0000-0000-0000-000000000001/pull-requests"
        ),
        post_webhook(scm_down, "opened"),
        connect_repository(repositories_client(FakeUnitOfWork(), FakeInstallationCatalog([API])), "999"),
        connect_repository(repositories_client(FakeUnitOfWork(), FakeInstallationCatalog([API]))),
        connect_repository(
            repositories_client(connected, FakeInstallationCatalog([API], managers={(API.full_name, SIGNED_IN.login)}))
        ),
        client.get("/api/v1/nope"),
        client.get("/api/v1/a%20b"),
        client.delete("/api/v1/webhooks/github"),
        client.get("/api/v1/_boom"),
        client.get("/api/v1/_throttled"),
        client.get("/api/v1/auth/me"),
        client.post("/api/v1/auth/logout"),
        client.post("/api/v1/webhooks/github", content=b"{}"),
        client.post("/api/v1/webhooks/gitlab", content=b"{}"),
        client.get("/api/v1/auth/bitbucket/login"),
        _signed_in_client().post("/api/v1/reviews", json={}, headers={"Origin": ORIGIN}),
        _signed_in_client().post(
            "/api/v1/reviews",
            json=REVIEW_BODY,
            headers={"Origin": ORIGIN},
        ),
    ]
    seen = set()
    for response in responses:
        body = response.json()
        problem = AnyProblem.model_validate(body).root
        assert type(problem) is PROBLEM_MODELS[ErrorCode(body["code"])], response.request.url
        assert problem.model_dump(mode="json") == body, response.request.url
        assert TRACE_ID.fullmatch(response.headers["X-Trace-ID"])
        assert response.headers["X-Trace-ID"] == body["trace_id"]
        seen.add(body["code"])
    assert seen == {code.value for code in ErrorCode} - {ErrorCode.REVIEW_ALREADY_ACTIVE.value}
