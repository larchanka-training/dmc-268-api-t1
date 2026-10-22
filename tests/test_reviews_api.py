"""Контракт `GET /api/v1/reviews/{id}` и `/steps`. Без БД."""

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient

from app.api.dependencies import get_unit_of_work
from app.api.factory import create_app
from app.config import Settings
from app.domain.entities import (
    DiffAnchor,
    Finding,
    MergeRequest,
    PublishedComment,
    Repository,
    ReviewRun,
    RuleRef,
)
from app.domain.enums import (
    CommentKind,
    DiffSide,
    FindingCategory,
    FindingSeverity,
    MergeRequestState,
    Provider,
    ReviewRunStage,
    ReviewRunStatus,
    TriggerSource,
    VerificationOutcome,
)
from app.domain.ids import new_id
from app.domain.run_steps import finish_step, start_step

from .fakes.auth import sign_in
from .fakes.unit_of_work import FakeUnitOfWork

SETTINGS = Settings(
    database_url="postgresql+psycopg://test:test@localhost/test",
    rabbitmq_url="amqp://guest:guest@localhost//",
    github_webhook_secret="test-secret",
)

NOW = datetime(2026, 9, 29, tzinfo=UTC)


def _client(uow: FakeUnitOfWork) -> TestClient:
    app = create_app(SETTINGS)
    sign_in(app)

    def override_uow() -> Iterator[FakeUnitOfWork]:
        yield uow

    app.dependency_overrides[get_unit_of_work] = override_uow
    return TestClient(app)


def _merge_request(uow: FakeUnitOfWork, provider: Provider = Provider.GITHUB) -> MergeRequest:
    repository = Repository(
        id=new_id(),
        provider=provider,
        provider_id="123",
        full_name="octo-org/api",
        default_branch="main",
        auto_review_enabled=True,
        created_at=NOW,
        updated_at=NOW,
    )
    uow.repositories.add(repository)
    merge_request = MergeRequest(
        id=new_id(),
        repository_id=repository.id,
        number=42,
        title="Вход через GitHub",
        description="",
        author="octocat",
        source_branch="feat/login",
        target_branch="develop",
        head_sha="abc123",
        state=MergeRequestState.OPEN,
        created_at=NOW,
        updated_at=NOW,
    )
    uow.merge_requests.add(merge_request)
    return merge_request


def _add_run(uow: FakeUnitOfWork, **overrides: object) -> ReviewRun:
    """Прогон вместе с его запросом на изменение и репозиторием — как гарантирует внешний ключ."""
    run = _run(merge_request_id=_merge_request(uow).id, **overrides)
    uow.review_runs.add(run)
    return run


def _comment(run: ReviewRun, published_at: datetime, kind: CommentKind = CommentKind.INLINE) -> PublishedComment:
    return PublishedComment(
        id=new_id(),
        review_run_id=run.id,
        provider_comment_id=str(new_id().int % 10**10),
        kind=kind,
        published_at=published_at,
        created_at=published_at,
        updated_at=published_at,
        finding_id=None if kind is CommentKind.SUMMARY else new_id(),
    )


def _run(**overrides: object) -> ReviewRun:
    fields = {
        "id": new_id(),
        "merge_request_id": new_id(),
        "head_sha": "abc123",
        "status": ReviewRunStatus.COMPLETED,
        "trigger": TriggerSource.WEBHOOK,
        "last_progress_at": NOW,
        "created_at": NOW,
        "updated_at": NOW,
        "model": "stub-llm",
        "tokens_used": 0,
    }
    fields.update(overrides)
    return ReviewRun(**fields)  # type: ignore[arg-type]


def test_returns_a_run_with_its_findings() -> None:
    uow = FakeUnitOfWork()
    run = _add_run(uow)
    uow.findings.add(
        Finding(
            id=new_id(),
            review_run_id=run.id,
            anchor=DiffAnchor(file_path="a.py", side=DiffSide.NEW, new_line=1),
            category=FindingCategory.READABILITY,
            severity=FindingSeverity.LOW,
            message="сообщение",
            created_at=NOW,
            updated_at=NOW,
        )
    )
    client = _client(uow)

    response = client.get(f"/api/v1/reviews/{run.id}")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "completed"
    assert body["model"] == "stub-llm"
    assert len(body["findings"]) == 1
    assert body["findings"][0]["message"] == "сообщение"


def test_a_run_with_no_findings_returns_an_empty_list() -> None:
    uow = FakeUnitOfWork()
    run = _add_run(uow)
    client = _client(uow)

    response = client.get(f"/api/v1/reviews/{run.id}")

    assert response.status_code == 200
    assert response.json()["findings"] == []


def test_unknown_run_is_404() -> None:
    uow = FakeUnitOfWork()
    client = _client(uow)

    response = client.get(f"/api/v1/reviews/{new_id()}")

    assert response.status_code == 404
    assert response.headers["content-type"] == "application/problem+json"
    assert response.json()["code"] == "REVIEW_NOT_FOUND"


def test_a_malformed_id_is_a_validation_error() -> None:
    response = _client(FakeUnitOfWork()).get("/api/v1/reviews/not-a-uuid")

    assert response.status_code == 422
    body = response.json()
    assert body["code"] == "VALIDATION_ERROR"
    assert body["errors"] == [{"where": "path.review_run_id", "reason": "ожидается UUID"}]
    assert "not-a-uuid" not in body["detail"]
    assert "not-a-uuid" not in str(body["errors"])


def test_a_running_run_reports_its_last_progress_and_no_failed_stage() -> None:
    uow = FakeUnitOfWork()
    run = _add_run(uow, status=ReviewRunStatus.ANALYSING, model=None, tokens_used=None)
    client = _client(uow)

    body = client.get(f"/api/v1/reviews/{run.id}").json()

    assert body["last_progress_at"] == "2026-09-29T00:00:00Z"
    assert body["failed_stage"] is None


def test_a_failed_run_reports_the_stage_it_failed_in() -> None:
    uow = FakeUnitOfWork()
    run = _add_run(
        uow,
        status=ReviewRunStatus.FAILED,
        failed_stage=ReviewRunStatus.ANALYSING,
        failure_reason="модель недоступна",
    )
    client = _client(uow)

    body = client.get(f"/api/v1/reviews/{run.id}").json()

    assert body["failed_stage"] == "analysing"
    assert body["failure_reason"] == "модель недоступна"


def _finding(run: ReviewRun, rule: RuleRef | None) -> Finding:
    return Finding(
        id=new_id(),
        review_run_id=run.id,
        anchor=DiffAnchor(file_path="a.py", side=DiffSide.NEW, new_line=1),
        category=FindingCategory.CORRECTNESS,
        severity=FindingSeverity.MEDIUM,
        message="m",
        created_at=NOW,
        updated_at=NOW,
        rule=rule,
    )


def test_a_finding_by_an_owner_rule_reports_the_rule() -> None:
    uow = FakeUnitOfWork()
    run = _add_run(uow)
    uow.findings.add(_finding(run, RuleRef("Error Handling Standards", 2, "Текст ошибки воспроизводим")))

    body = _client(uow).get(f"/api/v1/reviews/{run.id}").json()

    assert body["findings"][0]["rule"] == {
        "name": "Error Handling Standards",
        "check": 2,
        "check_text": "Текст ошибки воспроизводим",
    }


def test_a_standard_finding_reports_no_rule() -> None:
    uow = FakeUnitOfWork()
    run = _add_run(uow)
    uow.findings.add(_finding(run, None))

    body = _client(uow).get(f"/api/v1/reviews/{run.id}").json()

    assert body["findings"][0]["rule"] is None


def test_a_run_reports_its_verification_outcome() -> None:
    uow = FakeUnitOfWork()
    run = _add_run(uow, verification=VerificationOutcome.UNAVAILABLE)

    body = _client(uow).get(f"/api/v1/reviews/{run.id}").json()

    assert body["verification"] == "unavailable"
    assert body["verifier_dropped_findings"] == 0


def test_a_run_before_analysis_has_no_verification_outcome() -> None:
    uow = FakeUnitOfWork()
    run = _add_run(uow, status=ReviewRunStatus.BUILDING_CONTEXT, model=None, tokens_used=None)

    body = _client(uow).get(f"/api/v1/reviews/{run.id}").json()

    assert body["verification"] is None


def test_a_run_carries_its_rejected_findings_count() -> None:
    uow = FakeUnitOfWork()
    run = _add_run(uow, rejected_findings=3)

    body = _client(uow).get(f"/api/v1/reviews/{run.id}").json()

    assert body["rejected_findings"] == 3


def test_a_run_carries_a_summary_of_its_merge_request() -> None:
    uow = FakeUnitOfWork()
    run = _add_run(uow)
    merge_request = uow.merge_requests.get(run.merge_request_id)
    assert merge_request is not None

    body = _client(uow).get(f"/api/v1/reviews/{run.id}").json()

    assert body["merge_request_id"] == str(merge_request.id)
    assert body["merge_request"] == {
        "id": str(merge_request.id),
        "number": 42,
        "title": "Вход через GitHub",
        "author": "octocat",
        "source_branch": "feat/login",
        "target_branch": "develop",
        "state": "open",
        "url": "https://github.com/octo-org/api/pull/42",
        "repository": {
            "id": str(merge_request.repository_id),
            "full_name": "octo-org/api",
            "provider": "github",
        },
    }


def test_a_merge_request_outside_github_has_no_url() -> None:
    uow = FakeUnitOfWork()
    run = _run(merge_request_id=_merge_request(uow, Provider.GITLAB).id)
    uow.review_runs.add(run)

    body = _client(uow).get(f"/api/v1/reviews/{run.id}").json()

    assert body["merge_request"]["url"] is None
    assert body["merge_request"]["repository"]["provider"] == "gitlab"


def test_published_comments_come_in_publication_order() -> None:
    uow = FakeUnitOfWork()
    run = _add_run(uow)
    later = _comment(run, datetime(2026, 9, 29, 0, 5, tzinfo=UTC))
    summary = _comment(run, NOW, CommentKind.SUMMARY)
    uow.published_comments.add(later)
    uow.published_comments.add(summary)

    body = _client(uow).get(f"/api/v1/reviews/{run.id}").json()

    assert body["published_comments"] == [
        {
            "id": str(summary.id),
            "finding_id": None,
            "provider_comment_id": summary.provider_comment_id,
            "kind": "summary",
            "published_at": "2026-09-29T00:00:00Z",
        },
        {
            "id": str(later.id),
            "finding_id": str(later.finding_id),
            "provider_comment_id": later.provider_comment_id,
            "kind": "inline",
            "published_at": "2026-09-29T00:05:00Z",
        },
    ]


def test_a_run_that_published_nothing_has_no_comments() -> None:
    uow = FakeUnitOfWork()
    run = _add_run(uow)

    body = _client(uow).get(f"/api/v1/reviews/{run.id}").json()

    assert body["published_comments"] == []


def test_steps_come_in_start_order_with_their_outcome() -> None:
    uow = FakeUnitOfWork()
    run = _add_run(uow)

    def step(stage: ReviewRunStage, seconds: int):
        return start_step(run.id, stage, started_at=NOW + timedelta(seconds=seconds), step_id=new_id())

    analysing_retry = step(ReviewRunStage.ANALYSING, 30)
    for added in (
        analysing_retry,
        finish_step(step(ReviewRunStage.FETCHING_DIFF, 0), duration_seconds=1.25, tokens_used=None),
        finish_step(
            step(ReviewRunStage.ANALYSING, 10), duration_seconds=20.0, tokens_used=40, error_code="LLM_TIMEOUT"
        ),
    ):
        uow.review_run_steps.add(added)
    # Шаг другого прогона в ответ не попадает.
    uow.review_run_steps.add(
        start_step(_add_run(uow).id, ReviewRunStage.PUBLISHING, started_at=NOW, step_id=new_id())
    )

    response = _client(uow).get(f"/api/v1/reviews/{run.id}/steps")

    assert response.status_code == 200
    assert response.json() == {
        "items": [
            {
                "stage": "fetching_diff",
                "status": "completed",
                "started_at": "2026-09-29T00:00:00Z",
                "duration_seconds": 1.25,
                "tokens_used": None,
                "error_code": None,
            },
            {
                "stage": "analysing",
                "status": "failed",
                "started_at": "2026-09-29T00:00:10Z",
                "duration_seconds": 20.0,
                "tokens_used": 40,
                "error_code": "LLM_TIMEOUT",
            },
            {
                "stage": "analysing",
                "status": "running",
                "started_at": "2026-09-29T00:00:30Z",
                "duration_seconds": None,
                "tokens_used": None,
                "error_code": None,
            },
        ]
    }


def test_a_run_without_steps_has_an_empty_list() -> None:
    uow = FakeUnitOfWork()
    run = _add_run(uow, status=ReviewRunStatus.QUEUED)

    response = _client(uow).get(f"/api/v1/reviews/{run.id}/steps")

    assert response.status_code == 200
    assert response.json() == {"items": []}


def test_steps_of_an_unknown_run_are_404() -> None:
    run_id = new_id()

    response = _client(FakeUnitOfWork()).get(f"/api/v1/reviews/{run_id}/steps")

    assert response.status_code == 404
    assert response.headers["content-type"] == "application/problem+json"
    assert response.json()["code"] == "REVIEW_NOT_FOUND"
    assert response.json()["instance"] == f"/api/v1/reviews/{run_id}/steps"


def test_events_path_is_not_taken_for_a_run_id() -> None:
    """`/reviews/events` — общий поток, а не прогон с id `events`; `/reviews/{uuid}` — по-прежнему прогон."""
    uow = FakeUnitOfWork()
    run = _add_run(uow)
    client = _client(uow)

    stream = client.get("/api/v1/reviews/events")
    by_id = client.get(f"/api/v1/reviews/{run.id}")

    assert stream.status_code == 501
    assert stream.headers["content-type"] == "application/problem+json"
    assert stream.json()["code"] == "NOT_IMPLEMENTED"
    assert by_id.status_code == 200
    assert by_id.json()["id"] == str(run.id)
