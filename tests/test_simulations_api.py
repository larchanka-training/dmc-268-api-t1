"""Песочница правил: `POST /api/v1/simulations` и `GET /api/v1/simulations/{id}`. Без БД и брокера.

Сессия, `Origin` и схема тела - в `tests/test_problem_details.py`; здесь - что ручки
отвечают на допустимый запрос: запуск, лимиты, недоступный брокер, чтение автором.
"""

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

import pytest
from fastapi.testclient import TestClient

from app.api.dependencies import get_job_queue, get_unit_of_work_factory
from app.api.factory import create_app
from app.config import Settings
from app.domain.entities import (
    DiffAnchor,
    DiscardedFinding,
    LlmFinding,
    LlmRuleRef,
    RuleRef,
    Simulation,
    SimulationFinding,
    SimulationResult,
    Verdict,
)
from app.domain.enums import (
    DiffSide,
    DiscardStage,
    FindingCategory,
    FindingSeverity,
    SimulationSourceKind,
    SimulationStatus,
    VerificationOutcome,
    VerifierVerdict,
)
from app.domain.file_context import ContextOmission, ContextRule, FileContext
from app.domain.simulation import fail_simulation, new_simulation

from .fakes.auth import SIGNED_IN, sign_in
from .fakes.unit_of_work import FakeUnitOfWork
from .fakes.webhooks import FakeQueue

ORIGIN = "https://review.example"
PATH = "/api/v1/simulations"
CODE_BODY = {"source": {"kind": "code", "file_path": "app/billing.py", "content": "x = 1\n"}, "rules": ""}
GIT_DIFF = "diff --git a/app/x.py b/app/x.py\n--- a/app/x.py\n+++ b/app/x.py\n@@ -1 +1 @@\n-a\n+b\n"


def _client(uow: FakeUnitOfWork, queue: FakeQueue | None = None, **settings: Any) -> TestClient:
    app = create_app(
        Settings(
            database_url="postgresql+psycopg://test:test@localhost/test",
            rabbitmq_url="amqp://guest:guest@localhost//",
            github_webhook_secret="test-secret",
            public_origin=ORIGIN,
            **settings,
        )
    )
    sign_in(app)
    app.dependency_overrides[get_unit_of_work_factory] = lambda: lambda: uow
    app.dependency_overrides[get_job_queue] = lambda: queue or FakeQueue()
    return TestClient(app, base_url=ORIGIN, headers={"Origin": ORIGIN})


def _stored(uow: FakeUnitOfWork, *, user_id: UUID = SIGNED_IN.id, ago: timedelta = timedelta(0)) -> Simulation:
    simulation = new_simulation(
        simulation_id=UUID(int=len(uow.simulations.stored) + 1),
        user_id=user_id,
        source_kind=SimulationSourceKind.CODE,
        source="x = 1\n",
        rules="",
        file_path="app/billing.py",
        now=datetime.now(UTC) - ago,
        ttl=timedelta(days=1),
    )
    uow.simulations.add(simulation)
    return simulation


def test_a_launch_answers_202_with_a_queued_simulation_and_puts_it_on_the_queue() -> None:
    uow, queue = FakeUnitOfWork(), FakeQueue()

    response = _client(uow, queue).post(PATH, json=CODE_BODY)

    assert response.status_code == 202
    body = response.json()
    assert body["status"] == "queued"
    assert (body["error_code"], body["model"], body["tokens_used"], body["verification"]) == (None,) * 4
    assert (body["findings"], body["discarded"], body["context"]) == ([], [], [])
    (job,) = queue.simulations
    assert str(job.simulation_id) == body["id"]
    stored = uow.simulations.get(job.simulation_id)
    assert stored is not None and stored.user_id == SIGNED_IN.id


def test_input_exactly_at_the_limits_and_a_diff_with_empty_rules_are_accepted() -> None:
    uow = FakeUnitOfWork()
    at_limit = {"source": {**CODE_BODY["source"], "content": "x" * 131_072}, "rules": "x" * 32_768}  # type: ignore[dict-item]
    diff = {"source": {"kind": "diff", "diff": GIT_DIFF}, "rules": ""}

    assert _client(uow).post(PATH, json=at_limit).status_code == 202
    # Первая ещё не завершена: вторую запускает другой пользователь, а не этот.
    uow.simulations.restore({})
    assert _client(uow).post(PATH, json=diff).status_code == 202


def test_rules_above_the_byte_limit_are_refused_like_the_parser() -> None:
    uow = FakeUnitOfWork()

    response = _client(uow, rules_max_bytes=10).post(PATH, json={**CODE_BODY, "rules": "й" * 6})

    assert response.status_code == 422
    assert response.json()["errors"] == [{"where": "body.rules", "reason": "размер больше 10 байт в UTF-8"}]
    assert uow.simulations.stored == {}


@pytest.mark.parametrize(
    "diff",
    ["это не дифф", GIT_DIFF.split("\n", 1)[1]],
    ids=["не дифф", "unified diff без строки diff --git"],
)
def test_input_with_nothing_to_review_is_refused_before_it_is_stored(diff: str) -> None:
    """Разбор - тот же, что у ревью: формат git diff; причина подсказывает, чего не хватило."""
    uow, queue = FakeUnitOfWork(), FakeQueue()

    response = _client(uow, queue).post(PATH, json={"source": {"kind": "diff", "diff": diff}, "rules": ""})

    assert response.status_code == 422
    assert response.json()["code"] == "VALIDATION_ERROR"
    [error] = response.json()["errors"]
    assert error["where"] == "body.source"
    assert "diff --git" in error["reason"]
    assert uow.simulations.stored == {} and queue.simulations == []


def test_an_unfinished_simulation_is_answered_with_429_and_the_poll_interval() -> None:
    uow = FakeUnitOfWork()
    _stored(uow)

    response = _client(uow).post(PATH, json=CODE_BODY)

    assert response.status_code == 429
    assert response.json()["code"] == "RATE_LIMITED"
    assert response.headers["Retry-After"] == "5"


def test_the_hourly_limit_is_answered_with_429_until_the_oldest_launch_leaves_the_window() -> None:
    uow = FakeUnitOfWork()
    for minutes in (50, 20):
        simulation = _stored(uow, ago=timedelta(minutes=minutes))
        uow.simulations.update(fail_simulation(simulation, "LLM_TIMEOUT", simulation.created_at).unwrap())

    response = _client(uow, simulation_max_per_hour=2).post(PATH, json=CODE_BODY)

    assert response.status_code == 429
    # Самый старый запуск выйдет из окна примерно через 10 минут.
    assert 595 <= int(response.headers["Retry-After"]) <= 600


def test_an_unavailable_broker_still_answers_202_with_a_failed_simulation() -> None:
    uow = FakeUnitOfWork()

    response = _client(uow, FakeQueue(error=ConnectionError("брокер недоступен"))).post(PATH, json=CODE_BODY)

    assert response.status_code == 202
    assert (response.json()["status"], response.json()["error_code"]) == ("failed", "QUEUE_UNAVAILABLE")


def test_the_author_reads_the_result_with_verdicts_discarded_and_context() -> None:
    uow = FakeUnitOfWork()
    simulation = _stored(uow)
    anchor = DiffAnchor("app/billing.py", DiffSide.NEW, new_line=1)
    result = SimulationResult(
        findings=(
            SimulationFinding(
                id=UUID(int=50),
                anchor=anchor,
                category=FindingCategory.SECURITY,
                severity=FindingSeverity.CRITICAL,
                message="Токен в коде",
                confidence=0.9,
                rule=RuleRef("Secrets", 1, "Без токенов в коде"),
                verdict=Verdict(VerifierVerdict.DROP, "похоже на тестовый"),
            ),
        ),
        discarded=(
            DiscardedFinding(
                DiscardStage.SCREENING,
                "правило не показывали модели",
                LlmFinding(
                    anchor=DiffAnchor("app/billing.py", DiffSide.NEW, new_line=40),
                    category=FindingCategory.READABILITY,
                    severity=FindingSeverity.LOW,
                    message="Имя",
                    rule=LlmRuleRef("Naming", 2),
                ),
            ),
        ),
        context=(
            FileContext(
                file_path="app/billing.py",
                hunks=("@@ -0,0 +1 @@\n+x = 1",),
                scopes=None,
                imports=None,
                rules=(ContextRule("Secrets", ("Без токенов в коде",)),),
                omitted=(ContextOmission("ast", "too_large"),),
            ),
        ),
    )
    done = replace(
        simulation,
        status=SimulationStatus.COMPLETED,
        source=None,
        rules=None,
        result=result,
        model="glm-5.3",
        tokens_used=1500,
        verification=VerificationOutcome.VERIFIED,
    )
    uow.simulations.restore({done.id: done})

    response = _client(uow).get(f"{PATH}/{done.id}")

    assert response.status_code == 200
    body = response.json()
    assert (body["status"], body["model"], body["tokens_used"], body["verification"]) == (
        "completed",
        "glm-5.3",
        1500,
        "verified",
    )
    assert body["findings"] == [
        {
            "id": str(UUID(int=50)),
            "file_path": "app/billing.py",
            "side": "new",
            "old_line": None,
            "new_line": 1,
            "category": "security",
            "severity": "critical",
            "message": "Токен в коде",
            "suggestion": None,
            "confidence": 0.9,
            "rule": {"name": "Secrets", "check": 1, "check_text": "Без токенов в коде"},
            "verdict": {"verdict": "drop", "reason": "похоже на тестовый"},
        }
    ]
    assert body["discarded"] == [
        {
            "stage": "screening",
            "reason": "правило не показывали модели",
            "file_path": "app/billing.py",
            "side": "new",
            "line": 40,
            "category": "readability",
            "severity": "low",
            "message": "Имя",
            "rule": {"name": "Naming", "check": 2},
        }
    ]
    assert body["context"] == [
        {
            "file_path": "app/billing.py",
            "hunks": ["@@ -0,0 +1 @@\n+x = 1"],
            "scopes": None,
            "imports": None,
            "rules": [{"name": "Secrets", "checks": ["Без токенов в коде"]}],
            "omitted": [{"part": "ast", "reason": "too_large"}],
        }
    ]


def test_another_users_missing_and_expired_simulations_are_the_same_404() -> None:
    uow = FakeUnitOfWork()
    foreign = _stored(uow, user_id=UUID(int=999))
    expired = _stored(uow, ago=timedelta(days=2))
    client = _client(uow)

    for simulation_id in (foreign.id, expired.id, UUID(int=12345)):
        response = client.get(f"{PATH}/{simulation_id}")
        assert response.status_code == 404
        assert response.json()["code"] == "SIMULATION_NOT_FOUND"
