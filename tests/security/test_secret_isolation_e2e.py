"""Изоляция секретов: ни один секрет стенда не попадает в логи, спаны и записи прогона.

Сквозной прогон со сбоями, на которых секреты утекают чаще всего: провайдер
модели цитирует ключ в тексте ошибки, ключ отвергнут, ротация уходит на
следующий, GitHub отвечает 5xx на публикации. Секреты: ключи модели,
installation token GitHub App, секрет подписи вебхука, приватный ключ App.
"""

import logging
from collections.abc import Iterator

import httpx
import pytest
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from app.domain.enums import ReviewRunStatus

from ..chaos.scenario import accept
from ..conftest import requires_broker, requires_db
from ..e2e.stack import WEBHOOK_SECRET, Stack
from ..fakes.github_api import REPO_FULL_NAME, CreateReviewFailure, PullRequest
from ..fakes.llm_gateway import finding_json, review_response, verification_response
from ..fakes.llm_provider import FakeLlmProvider

pytestmark = [pytest.mark.integration, requires_db, requires_broker]

PR = 51
KEYS = ("sk-live-AAAAAAAAAAAAAAAAAAAAAAAA", "sk-live-BBBBBBBBBBBBBBBBBBBBBBBB")
CODE_PATH = "app/payments.py"
CODE = "def charge(amount, balance):\n    return balance - amount\n"


def _quoting(status: int, key: str) -> httpx.Response:
    """Провайдер цитирует ключ в описании ошибки - так делают некоторые прокси."""
    return httpx.Response(status, json={"error": {"message": f"Invalid API key: {key}"}})


def _pull(stack: Stack) -> PullRequest:
    return stack.github.open_pull(PR, base_sha="9a" * 20, head_sha="9b" * 20, head_files={CODE_PATH: CODE})


def _secrets(stack: Stack) -> list[str]:
    private_key = stack.settings.github_app_private_key.get_secret_value()
    # Строка тела PEM: заголовок `BEGIN …` секретом не является.
    key_line = next(line for line in private_key.splitlines() if line and "-----" not in line)
    return [*KEYS, WEBHOOK_SECRET, key_line, *stack.github._tokens]


def _span_texts(spans: InMemorySpanExporter) -> Iterator[str]:
    for span in spans.get_finished_spans():
        yield span.name
        yield str(span.status.description or "")
        yield from (str(v) for v in (span.attributes or {}).values())
        for event in span.events:
            yield from (str(v) for v in (event.attributes or {}).values())


def _assert_nowhere(stack: Stack, run_id: str, caplog: pytest.LogCaptureFixture, spans: InMemorySpanExporter) -> None:
    run = stack.run(run_id)
    records = [r.getMessage() for r in caplog.records] + [
        logging.Formatter().formatException(r.exc_info) for r in caplog.records if r.exc_info
    ]
    places = {
        "лог": "\n".join(records),
        "спаны": "\n".join(_span_texts(spans)),
        "failure_reason": run.failure_reason or "",
        "шаги": "\n".join(str(s) for s in stack.steps(run_id)),
    }
    secrets = _secrets(stack)
    assert stack.github._tokens, "installation token не выдавался - проверка пустая"
    leaks = [(where, secret[:12]) for where, text in places.items() for secret in secrets if secret in text]
    assert leaks == []


def test_provider_quoting_every_key_leaks_none_of_them(
    stack: Stack, caplog: pytest.LogCaptureFixture, spans: InMemorySpanExporter
) -> None:
    """Оба ключа отвергнуты с цитатой ключа, прогон `failed`: в логах, спанах и прогоне ключей нет."""
    provider = FakeLlmProvider([_quoting(401, KEYS[0]), _quoting(500, KEYS[1])])

    with caplog.at_level(logging.DEBUG):
        run_id = accept(stack, _pull(stack))
        stack.work(provider.gateway(KEYS), 1)

    assert stack.run(run_id).status is ReviewRunStatus.FAILED
    _assert_nowhere(stack, run_id, caplog, spans)


def test_successful_run_through_github_failures_leaks_no_secret(
    stack: Stack, caplog: pytest.LogCaptureFixture, spans: InMemorySpanExporter
) -> None:
    """Ключ отвергнут с цитатой, ротация на следующий, GitHub - 502 на создании ревью:
    прогон `completed`, секретов нигде нет, включая отладочный уровень http-клиентов."""
    review = review_response(
        finding_json(CODE_PATH, 2, "Нет проверки баланса.", category="correctness", severity="high")
    )
    provider = FakeLlmProvider([_quoting(401, KEYS[0]), review, verification_response(("F1", "keep"))])
    stack.github.create_review_failures = [CreateReviewFailure(502, stored=True)]

    with caplog.at_level(logging.DEBUG):
        run_id = accept(stack, _pull(stack))
        stack.work(provider.gateway(KEYS), 1)

    assert stack.run(run_id).status is ReviewRunStatus.COMPLETED
    assert len(stack.github.reviews_of(PR)) == 1
    assert ("POST", f"/repos/{REPO_FULL_NAME}/pulls/{PR}/reviews") in stack.github.requests
    _assert_nowhere(stack, run_id, caplog, spans)
