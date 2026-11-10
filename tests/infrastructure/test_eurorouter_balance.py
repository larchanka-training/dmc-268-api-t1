"""`EurorouterBalance`: остаток из `/credits`, сбои опроса и кэш между опросами."""

import logging
from collections.abc import Callable

import httpx
import pytest
from pydantic import SecretStr

from app.infrastructure.llm.eurorouter_balance import EurorouterBalance

KEY = "sk-secret-one"
# Тело `/credits` Eurorouter: суммы в евро строками, 10 знаков.
# Вид ответа сверен с живым `/credits` Eurorouter: поля внутри `data`.
CREDITS = {"data": {"total_credits": "20.0000000000", "total_usage": "6.9039961619"}}


def _balance(
    handler: Callable[[httpx.Request], httpx.Response], clock: list[float] | None = None
) -> EurorouterBalance:
    client = httpx.Client(
        base_url="https://eurorouter.example/api/v1", transport=httpx.MockTransport(handler)
    )
    now = clock if clock is not None else [0.0]
    return EurorouterBalance(client, SecretStr(KEY), ttl_seconds=300, monotonic=lambda: now[0])


def test_the_balance_is_credits_minus_usage() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=CREDITS)

    assert _balance(handler)() == pytest.approx(13.0960038381)
    (request,) = seen
    assert str(request.url) == "https://eurorouter.example/api/v1/credits"
    assert request.headers["Authorization"] == f"Bearer {KEY}"


def _refused(request: httpx.Request) -> httpx.Response:
    raise httpx.ConnectError("refused", request=request)


@pytest.mark.parametrize(
    ("handler", "warning"),
    [
        (_refused, "Eurorouter: баланс не получен (ConnectError)"),
        (lambda r: httpx.Response(401, text=f"bad key {KEY}"), "Eurorouter: баланс не получен (HTTP 401)"),
        (lambda r: httpx.Response(200, text="не json"), "Eurorouter: баланс не разобран"),
        (lambda r: httpx.Response(200, json={"data": {"total_credits": "20"}}), "Eurorouter: баланс не разобран"),
        (lambda r: httpx.Response(200, json={"data": {**CREDITS["data"], "total_usage": "много"}}), "Eurorouter: баланс не разобран"),
        (lambda r: httpx.Response(200, json=[]), "Eurorouter: баланс не разобран"),
    ],
    ids=["network", "not-200", "not-json", "no-usage", "not-a-number", "not-an-object"],
)
def test_a_failed_poll_gives_no_balance_and_one_warning(
    handler: Callable[[httpx.Request], httpx.Response], warning: str, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.WARNING):
        assert _balance(handler)() is None

    assert [record.getMessage() for record in caplog.records] == [warning]
    assert KEY not in caplog.text


def test_the_provider_is_polled_at_most_once_per_ttl() -> None:
    replies = [
        httpx.Response(200, json=CREDITS),
        httpx.Response(503),
        httpx.Response(200, json={"data": {"total_credits": "20", "total_usage": "19.5"}}),
    ]
    clock = [1000.0]
    balance = _balance(lambda request: replies.pop(0), clock)

    assert balance() == pytest.approx(13.0960038381)
    clock[0] += 299
    # Внутри TTL — прошлый результат, без запроса.
    assert balance() == pytest.approx(13.0960038381)
    assert len(replies) == 2
    clock[0] += 1
    assert balance() is None
    clock[0] += 100
    # Сбой тоже держится до следующего опроса: лежащий провайдер не опрашивается чаще.
    assert balance() is None
    clock[0] += 200
    assert balance() == pytest.approx(0.5)
