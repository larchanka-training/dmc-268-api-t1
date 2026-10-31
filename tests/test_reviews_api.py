"""Контракт `GET /api/v1/reviews/{id}`, `/steps` и `/diff`: данные и видимость прогона. Без БД и сети."""

from collections.abc import Iterator
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.api.dependencies import (
    get_cabinet_vcs_gateway,
    get_installation_catalog,
    get_token_provider,
    get_unit_of_work,
)
from app.api.factory import create_app
from app.api.reviews import review_run_list_item
from app.api.session import current_session
from app.application.use_cases.auth import SignedIn
from app.application.use_cases.run_diff import RUN_DIFF_MAX_LINES
from app.config import Settings
from app.domain.entities import (
    DiffAnchor,
    Finding,
    InstallationRepository,
    MergeRequest,
    PublishedComment,
    Repository,
    RepositoryPermissions,
    ReviewRun,
    ReviewRunListEntry,
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
from app.domain.provider_token import ProviderTokenRejected, token_from_grant
from app.domain.run_steps import finish_step, start_step
from app.domain.session import open_session
from app.domain.vcs_errors import VcsError, VcsFileTooLargeError, VcsNotFoundError

from .fakes.auth import SIGNED_IN, sign_in_with_session
from .fakes.installation_catalog import FakeInstallationCatalog
from .fakes.oauth import EXPIRING_GRANT, REFRESHED_GRANT, FakeOAuthProvider
from .fakes.unit_of_work import FakeUnitOfWork
from .fakes.webhooks import FakeVcs

SETTINGS = Settings(
    database_url="postgresql+psycopg://test:test@localhost/test",
    rabbitmq_url="amqp://guest:guest@localhost//",
    github_webhook_secret="test-secret",
)

NOW = datetime(2026, 9, 29, tzinfo=UTC)

READ = RepositoryPermissions(admin=False, maintain=False, push=False, triage=False)
# GitHub по токену пользователя отдаёт репозиторий прогонов тестов (`provider_id` 123) — прогон виден.
VISIBLE = InstallationRepository("123", "octo-org/api", "main", READ)


def _client(
    uow: FakeUnitOfWork,
    catalog: FakeInstallationCatalog | None = None,
    vcs: FakeVcs | None = None,
    settings: Settings = SETTINGS,
) -> TestClient:
    """Клиент вошедшего пользователя; GitHub — фейки: каталог установки и шлюз кабинета (дифф прогона)."""
    app = create_app(settings)
    cookies = sign_in_with_session(app, uow)

    def override_uow() -> Iterator[FakeUnitOfWork]:
        yield uow

    app.dependency_overrides[get_unit_of_work] = override_uow
    app.dependency_overrides[get_installation_catalog] = lambda: catalog or FakeInstallationCatalog([VISIBLE])
    app.dependency_overrides[get_token_provider] = lambda: FakeOAuthProvider()
    gateway = vcs or FakeVcs()
    app.dependency_overrides[get_cabinet_vcs_gateway] = lambda: gateway
    return TestClient(app, cookies=cookies)


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
        connected_at=NOW,
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


def test_a_finding_covered_by_an_earlier_thread_reports_that_finding() -> None:
    """#148: повтор замечания не публиковался - находка ссылается на находку треда."""
    uow = FakeUnitOfWork()
    run = _add_run(uow)
    earlier = new_id()
    uow.findings.add(replace(_finding(run, None), covered_by_finding_id=earlier))

    body = _client(uow).get(f"/api/v1/reviews/{run.id}").json()

    assert body["findings"][0]["covered_by_finding_id"] == str(earlier)


def test_a_published_finding_is_covered_by_nothing() -> None:
    uow = FakeUnitOfWork()
    run = _add_run(uow)
    uow.findings.add(_finding(run, None))

    body = _client(uow).get(f"/api/v1/reviews/{run.id}").json()

    assert body["findings"][0]["covered_by_finding_id"] is None


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
    merge_request = _merge_request(uow, Provider.GITLAB)
    repository = uow.repositories.get(merge_request.repository_id)
    assert repository is not None

    item = review_run_list_item(ReviewRunListEntry(_run(merge_request_id=merge_request.id), merge_request, repository, 0))

    assert item.merge_request.url is None
    assert item.merge_request.repository.provider == "gitlab"


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


def test_old_events_path_is_a_run_id_now() -> None:
    """Поток переехал на `/events`: `/reviews/events` — прогон с неверным id, `422`, а не поток."""
    response = _client(FakeUnitOfWork()).get("/api/v1/reviews/events")

    assert response.status_code == 422
    assert response.json()["errors"] == [{"where": "path.review_run_id", "reason": "ожидается UUID"}]


def test_list_item_is_built_from_the_run_its_merge_request_and_findings() -> None:
    """Строка списка для потока событий: прогон, запрос на изменение со ссылкой и число находок из записи пачки."""
    uow = FakeUnitOfWork()
    run = _add_run(uow)
    merge_request = uow.merge_requests.get(run.merge_request_id)
    assert merge_request is not None
    repository = uow.repositories.get(merge_request.repository_id)
    assert repository is not None

    item = review_run_list_item(ReviewRunListEntry(run, merge_request, repository, findings_count=2))

    assert item.id == run.id
    assert item.last_progress_at == NOW
    assert item.findings_count == 2
    assert item.merge_request.url == "https://github.com/octo-org/api/pull/42"
    assert item.merge_request.repository.full_name == "octo-org/api"


# --- видимость прогона: то же правило, что у списка и потока (#127) ---------------

RUN_PATHS = ("", "/steps", "/diff")


@pytest.mark.parametrize("suffix", RUN_PATHS)
def test_a_run_of_a_repository_hidden_on_github_is_404_like_a_missing_one(suffix: str) -> None:
    """Чужой прогон отвечает тем же `404`, что несуществующий: ответ не подтверждает, что id есть."""
    uow = FakeUnitOfWork()
    run = _add_run(uow)
    hidden = _client(uow, FakeInstallationCatalog([InstallationRepository("999", "octo-org/other", "main", READ)]))

    response = hidden.get(f"/api/v1/reviews/{run.id}{suffix}")
    missing = hidden.get(f"/api/v1/reviews/{new_id()}{suffix}")

    assert response.status_code == missing.status_code == 404
    assert response.json()["code"] == "REVIEW_NOT_FOUND"
    assert _without_instance(response) == _without_instance(missing)
    assert _headers(response) == _headers(missing)


def _without_instance(response: Any) -> dict[str, Any]:
    """Тело ответа без `instance` и `trace_id`: путь с id и трасса у двух ответов разные по определению."""
    return {key: value for key, value in response.json().items() if key not in {"instance", "trace_id"}}


def _headers(response: Any) -> dict[str, str]:
    """Заголовки ответа без тех, что различаются у любых двух запросов."""
    return {key: value for key, value in response.headers.items() if key.lower() not in {"x-trace-id", "content-length"}}


@pytest.mark.parametrize("suffix", RUN_PATHS)
def test_a_run_outside_github_is_visible_to_nobody(suffix: str) -> None:
    """Кабинет видит репозитории только через GitHub: id у другого провайдера может совпасть, но прогон не виден."""
    uow = FakeUnitOfWork()
    run = _run(merge_request_id=_merge_request(uow, Provider.GITLAB).id)
    uow.review_runs.add(run)

    assert _client(uow).get(f"/api/v1/reviews/{run.id}{suffix}").status_code == 404


@pytest.mark.parametrize("suffix", RUN_PATHS)
def test_a_missing_run_asks_github_like_an_existing_one(suffix: str) -> None:
    """GitHub спрашиваем и для несуществующего id: иначе сбой GitHub или задержка выдали бы, что id есть."""
    catalog = FakeInstallationCatalog([VISIBLE])

    response = _client(FakeUnitOfWork(), catalog).get(f"/api/v1/reviews/{new_id()}{suffix}")

    assert response.status_code == 404
    assert catalog.calls != []


@pytest.mark.parametrize("suffix", RUN_PATHS)
def test_github_failure_hides_whether_the_run_exists(suffix: str) -> None:
    """GitHub лёг — `502` и для существующего прогона, и для несуществующего."""
    uow = FakeUnitOfWork()
    run = _add_run(uow)
    client = _client(uow, FakeInstallationCatalog(error=VcsError("GitHub недоступен")))

    existing = client.get(f"/api/v1/reviews/{run.id}{suffix}")
    missing = client.get(f"/api/v1/reviews/{new_id()}{suffix}")

    assert existing.status_code == missing.status_code == 502
    assert _without_instance(existing) == _without_instance(missing)


@pytest.mark.parametrize("suffix", RUN_PATHS)
def test_a_token_revoked_at_github_closes_the_session(suffix: str) -> None:
    uow = FakeUnitOfWork()
    run = _add_run(uow)

    response = _client(uow, FakeInstallationCatalog(error=ProviderTokenRejected("401"))).get(
        f"/api/v1/reviews/{run.id}{suffix}"
    )

    assert response.status_code == 401
    assert response.json()["code"] == "UNAUTHENTICATED"
    assert any(
        header.startswith("session=") and "Max-Age=0" in header for header in response.headers.get_list("set-cookie")
    )
    assert uow.sessions.stored == []


@pytest.mark.parametrize("suffix", RUN_PATHS)
def test_github_failure_on_a_run_is_502(suffix: str) -> None:
    uow = FakeUnitOfWork()
    run = _add_run(uow)

    response = _client(uow, FakeInstallationCatalog(error=VcsError("GitHub недоступен"))).get(
        f"/api/v1/reviews/{run.id}{suffix}"
    )

    assert response.status_code == 502
    assert response.json()["code"] == "SCM_UNAVAILABLE"




# --- дифф прогона (change review-run-diff) -------------------------------------------

BASE_SHA = "base000"

# Дифф пары коммитов прогона: все случаи сценариев «Дифф прогона» в одном ответе GitHub.
RUN_DIFF = """\
diff --git a/app/main.py b/app/main.py
index 3f7d2e1..9c4a8b2 100644
--- a/app/main.py
+++ b/app/main.py
@@ -10,3 +10,3 @@ def main():
-    print("hi")
+    print("hello")
 return 0
diff --git a/app/new_module.py b/app/new_module.py
new file mode 100644
index 0000000..d95b1c2
--- /dev/null
+++ b/app/new_module.py
@@ -0,0 +1,2 @@
+first line
+second line
diff --git a/docs/gone.md b/docs/gone.md
deleted file mode 100644
index d95b1c2..0000000
--- a/docs/gone.md
+++ /dev/null
@@ -1,3 +0,0 @@
-one
-two
-three
diff --git a/old.py b/new.py
similarity index 90%
rename from old.py
rename to new.py
index 1a2b3c4..4d5e6f7 100644
--- a/old.py
+++ b/new.py
@@ -2,3 +2,3 @@
 context
-old line
+new line
 context
diff --git a/logo.png b/logo.png
index 3f2b1c0..8e9f0a1 100644
Binary files a/logo.png and b/logo.png differ
diff --git a/uv.lock b/uv.lock
--- a/uv.lock
+++ b/uv.lock
@@ -1 +1 @@
-version = 1
+version = 2
diff --git a/notes.txt b/notes.txt
--- a/notes.txt
+++ b/notes.txt
@@ -1,2 +1,2 @@
 first
-last line
\\ No newline at end of file
+last line changed
\\ No newline at end of file
"""


def _diff_files(response: Any) -> dict[str, dict[str, Any]]:
    assert response.status_code == 200, response.json()
    return {file["path"]: file for file in response.json()["files"]}


def _diff_calls(vcs: FakeVcs) -> list[tuple[Any, ...]]:
    return [call for call in vcs.calls if call[0] == "diff"]


def test_diff_of_a_visible_run_is_200_with_files_in_diff_order() -> None:
    """Сценарий «Дифф прогона»: `200` с `ReviewDiffResponse`, а не `501`; дифф — пары коммитов прогона."""
    uow = FakeUnitOfWork()
    run = _add_run(uow, base_sha=BASE_SHA)
    vcs = FakeVcs(diff=RUN_DIFF)
    catalog = FakeInstallationCatalog([VISIBLE], installation_ids={"octo-org/api": 77})

    response = _client(uow, catalog, vcs).get(f"/api/v1/reviews/{run.id}/diff")

    assert response.status_code == 200
    assert [file["path"] for file in response.json()["files"]] == [
        "app/main.py",
        "app/new_module.py",
        "docs/gone.md",
        "new.py",
        "logo.png",
        "uv.lock",
        "notes.txt",
    ]
    assert _diff_calls(vcs) == [("diff", "octo-org/api", BASE_SHA, "abc123", 77)]


def test_modified_file_has_numbered_lines_without_prefix() -> None:
    """Сценарий «Изменённый файл»."""
    uow = FakeUnitOfWork()
    run = _add_run(uow, base_sha=BASE_SHA)

    main = _diff_files(_client(uow, vcs=FakeVcs(diff=RUN_DIFF)).get(f"/api/v1/reviews/{run.id}/diff"))["app/main.py"]

    assert main == {
        "path": "app/main.py",
        "previous_path": None,
        "status": "modified",
        "is_binary": False,
        "language": "python",
        "additions": 1,
        "deletions": 1,
        "hunks": [
            {
                "header": "def main():",
                "old_start": 10,
                "old_count": 3,
                "new_start": 10,
                "new_count": 3,
                "lines": [
                    {"kind": "removed", "old_line": 10, "new_line": None, "content": '    print("hi")'},
                    {"kind": "added", "old_line": None, "new_line": 10, "content": '    print("hello")'},
                    {"kind": "context", "old_line": 11, "new_line": 11, "content": "return 0"},
                ],
            }
        ],
    }


def test_added_and_deleted_files_get_one_sided_counters() -> None:
    """Сценарий «Добавленный и удалённый файлы»."""
    uow = FakeUnitOfWork()
    run = _add_run(uow, base_sha=BASE_SHA)

    files = _diff_files(_client(uow, vcs=FakeVcs(diff=RUN_DIFF)).get(f"/api/v1/reviews/{run.id}/diff"))

    added, deleted = files["app/new_module.py"], files["docs/gone.md"]
    assert (added["status"], added["additions"], added["deletions"]) == ("added", 2, 0)
    assert (deleted["status"], deleted["additions"], deleted["deletions"]) == ("deleted", 0, 3)


def test_renamed_file_has_previous_path_and_others_do_not() -> None:
    """Сценарий «Переименование»."""
    uow = FakeUnitOfWork()
    run = _add_run(uow, base_sha=BASE_SHA)

    files = _diff_files(_client(uow, vcs=FakeVcs(diff=RUN_DIFF)).get(f"/api/v1/reviews/{run.id}/diff"))

    assert (files["new.py"]["previous_path"], files["new.py"]["status"]) == ("old.py", "renamed")
    assert [path for path, file in files.items() if file["previous_path"] is not None] == ["new.py"]


def test_binary_file_has_no_hunks() -> None:
    """Сценарий «Бинарный файл»."""
    uow = FakeUnitOfWork()
    run = _add_run(uow, base_sha=BASE_SHA)

    logo = _diff_files(_client(uow, vcs=FakeVcs(diff=RUN_DIFF)).get(f"/api/v1/reviews/{run.id}/diff"))["logo.png"]

    assert (logo["is_binary"], logo["hunks"], logo["additions"], logo["deletions"], logo["language"]) == (
        True,
        [],
        0,
        0,
        None,
    )


def test_file_filtered_out_of_review_is_still_in_the_diff() -> None:
    """Сценарий «Отфильтрованный ревью файл»: `uv.lock` модели не отправлялся, а в диффе есть."""
    uow = FakeUnitOfWork()
    run = _add_run(uow, base_sha=BASE_SHA)

    lock = _diff_files(_client(uow, vcs=FakeVcs(diff=RUN_DIFF)).get(f"/api/v1/reviews/{run.id}/diff"))["uv.lock"]

    assert [line["content"] for line in lock["hunks"][0]["lines"]] == ["version = 1", "version = 2"]


def test_no_newline_marker_is_not_a_line_and_does_not_shift_numbers() -> None:
    """Сценарий «Строка без перевода строки в конце файла»."""
    uow = FakeUnitOfWork()
    run = _add_run(uow, base_sha=BASE_SHA)

    notes = _diff_files(_client(uow, vcs=FakeVcs(diff=RUN_DIFF)).get(f"/api/v1/reviews/{run.id}/diff"))["notes.txt"]

    assert notes["hunks"][0]["lines"] == [
        {"kind": "context", "old_line": 1, "new_line": 1, "content": "first"},
        {"kind": "removed", "old_line": 2, "new_line": None, "content": "last line"},
        {"kind": "added", "old_line": None, "new_line": 2, "content": "last line changed"},
    ]


def test_diff_without_session_is_401() -> None:
    """Сценарий «Дифф без сессии»: ни GitHub, ни прогон не трогаются."""
    uow = FakeUnitOfWork()
    run = _add_run(uow, base_sha=BASE_SHA)
    vcs = FakeVcs(diff=RUN_DIFF)
    client = _client(uow, vcs=vcs)
    client.app.dependency_overrides.pop(current_session)  # type: ignore[attr-defined]
    client.cookies.clear()

    response = client.get(f"/api/v1/reviews/{run.id}/diff")

    assert response.status_code == 401
    assert response.json()["code"] == "UNAUTHENTICATED"
    assert vcs.calls == []


def test_diff_of_a_hidden_run_asks_neither_the_installation_nor_the_diff() -> None:
    """Сценарий «Шаги и дифф чужого прогона»: `404 REVIEW_NOT_FOUND` раньше установки, диффа и кэша."""
    uow = FakeUnitOfWork()
    run = _add_run(uow, base_sha=BASE_SHA)
    vcs = FakeVcs(diff=RUN_DIFF)
    catalog = FakeInstallationCatalog([InstallationRepository("999", "octo-org/other", "main", READ)])

    response = _client(uow, catalog, vcs).get(f"/api/v1/reviews/{run.id}/diff")

    assert response.status_code == 404
    assert response.json()["code"] == "REVIEW_NOT_FOUND"
    assert "repository_installation_id" not in catalog.calls
    assert vcs.calls == []


def test_hidden_run_does_not_get_a_cached_diff() -> None:
    """Видимость — до чтения кэша: дифф, закэшированный для видящего, невидящему не отдаётся."""
    uow = FakeUnitOfWork()
    run = _add_run(uow, base_sha=BASE_SHA)
    catalog = FakeInstallationCatalog(
        [VISIBLE], repositories_by_token={OTHER_TOKEN: [InstallationRepository("999", "octo-org/other", "main", READ)]}
    )
    client = _client(uow, catalog, FakeVcs(diff=RUN_DIFF))
    assert client.get(f"/api/v1/reviews/{run.id}/diff").status_code == 200

    _second_user(client, uow)
    response = client.get(f"/api/v1/reviews/{run.id}/diff")

    assert catalog.tokens[-1].reveal() == OTHER_TOKEN
    assert response.status_code == 404
    assert response.json()["code"] == "REVIEW_NOT_FOUND"


# --- отказы диффа прогона ---


def test_run_without_base_sha_is_409_without_github() -> None:
    """Сценарий «Прогон без base_sha»."""
    uow = FakeUnitOfWork()
    run = _add_run(uow)
    vcs = FakeVcs(diff=RUN_DIFF)
    catalog = FakeInstallationCatalog([VISIBLE])

    response = _client(uow, catalog, vcs).get(f"/api/v1/reviews/{run.id}/diff")

    assert response.status_code == 409
    assert response.headers["content-type"] == "application/problem+json"
    assert response.json()["code"] == "DIFF_UNAVAILABLE"
    assert "repository_installation_id" not in catalog.calls
    assert vcs.calls == []


def test_app_removed_from_the_repository_is_404_without_the_diff() -> None:
    """Сценарий «Приложение снято с репозитория»."""
    uow = FakeUnitOfWork()
    run = _add_run(uow, base_sha=BASE_SHA)
    vcs = FakeVcs(diff=RUN_DIFF)

    response = _client(uow, FakeInstallationCatalog([VISIBLE], not_installed={"octo-org/api"}), vcs).get(
        f"/api/v1/reviews/{run.id}/diff"
    )

    assert response.status_code == 404
    assert response.json()["code"] == "REPOSITORY_NOT_IN_INSTALLATION"
    assert vcs.calls == []


def test_commit_gone_at_the_provider_is_410() -> None:
    """Сценарий «Коммит прогона исчез у провайдера»."""
    uow = FakeUnitOfWork()
    run = _add_run(uow, base_sha=BASE_SHA)

    response = _client(uow, vcs=FakeVcs(error=VcsNotFoundError("HTTP 404"))).get(f"/api/v1/reviews/{run.id}/diff")

    assert response.status_code == 410
    assert response.json()["code"] == "COMMIT_NOT_FOUND"


def test_diff_over_the_limit_is_422_and_the_limit_comes_from_settings() -> None:
    """Сценарий «Дифф больше лимита»: лимит — `RUN_DIFF_MAX_BYTES`, шлюз дальше него не читает."""
    uow = FakeUnitOfWork()
    run = _add_run(uow, base_sha=BASE_SHA)
    small = SETTINGS.model_copy(update={"run_diff_max_bytes": len(RUN_DIFF.encode()) - 1})

    response = _client(uow, vcs=FakeVcs(diff=RUN_DIFF), settings=small).get(f"/api/v1/reviews/{run.id}/diff")

    assert response.status_code == 422
    assert response.json()["code"] == "DIFF_TOO_LARGE"


def test_gateway_too_large_error_is_422() -> None:
    """Обрыв тела на лимите в адаптере (`VcsFileTooLargeError`) — тот же `422`."""
    uow = FakeUnitOfWork()
    run = _add_run(uow, base_sha=BASE_SHA)

    response = _client(uow, vcs=FakeVcs(error=VcsFileTooLargeError("дифф: больше лимита"))).get(
        f"/api/v1/reviews/{run.id}/diff"
    )

    assert response.status_code == 422
    assert response.json()["code"] == "DIFF_TOO_LARGE"


def test_diff_over_the_line_limit_is_422_with_a_generic_detail() -> None:
    """Сценарий «Дифф больше лимита строк»: короткие строки укладываются в байты, но не в `RUN_DIFF_MAX_LINES`."""
    uow = FakeUnitOfWork()
    run = _add_run(uow, base_sha=BASE_SHA)
    added = RUN_DIFF_MAX_LINES + 1 - 5
    long_diff = (
        "diff --git a/many.txt b/many.txt\nnew file mode 100644\n--- /dev/null\n+++ b/many.txt\n"
        f"@@ -0,0 +1,{added} @@\n" + "+x\n" * added
    )

    response = _client(uow, vcs=FakeVcs(diff=long_diff)).get(f"/api/v1/reviews/{run.id}/diff")

    assert response.status_code == 422
    body = response.json()
    assert body["code"] == "DIFF_TOO_LARGE"
    assert f"{RUN_DIFF_MAX_LINES} строк" in body["detail"]


def test_github_failure_on_the_diff_is_502() -> None:
    """Сценарий «GitHub недоступен при загрузке диффа»: сбой запроса диффа."""
    uow = FakeUnitOfWork()
    run = _add_run(uow, base_sha=BASE_SHA)

    response = _client(uow, vcs=FakeVcs(error=VcsError("HTTP 502"))).get(f"/api/v1/reviews/{run.id}/diff")

    assert response.status_code == 502
    assert response.json()["code"] == "SCM_UNAVAILABLE"


def test_github_failure_on_the_installation_is_502_without_the_diff() -> None:
    """Сценарий «GitHub недоступен при загрузке диффа»: сбой запроса установки приложения."""
    uow = FakeUnitOfWork()
    run = _add_run(uow, base_sha=BASE_SHA)
    vcs = FakeVcs(diff=RUN_DIFF)
    catalog = FakeInstallationCatalog([VISIBLE], installation_id_error=VcsError("HTTP 503"))

    response = _client(uow, catalog, vcs).get(f"/api/v1/reviews/{run.id}/diff")

    assert response.status_code == 502
    assert response.json()["code"] == "SCM_UNAVAILABLE"
    assert vcs.calls == []


# --- кэш диффа на уровне ручки ---


OTHER_USER = replace(SIGNED_IN, id=new_id(), provider_user_id="777", login="hubot")
OTHER_SESSION_ID = new_id()


# Токен GitHub второго пользователя: по нему каталог отдаёт свой набор видимых (`repositories_by_token`).
OTHER_TOKEN = REFRESHED_GRANT.access_token.reveal()


def _second_user(client: TestClient, uow: FakeUnitOfWork) -> None:
    """Дальше запросы клиента идут от второго пользователя со своей сессией и токеном `OTHER_TOKEN`."""
    uow.users.add(OTHER_USER)
    uow.sessions.add(open_session(OTHER_SESSION_ID, OTHER_USER.id, "other-session", datetime.now(UTC)))
    uow.provider_tokens.save(OTHER_SESSION_ID, token_from_grant(REFRESHED_GRANT, datetime.now(UTC)))
    client.app.dependency_overrides[current_session] = lambda: SignedIn(user=OTHER_USER, session_id=OTHER_SESSION_ID)  # type: ignore[attr-defined]


def test_repeat_within_ttl_asks_github_once_and_answers_the_same() -> None:
    """Сценарий «Повтор в пределах TTL»: один запрос диффа и один — установки."""
    uow = FakeUnitOfWork()
    run = _add_run(uow, base_sha=BASE_SHA)
    vcs = FakeVcs(diff=RUN_DIFF)
    catalog = FakeInstallationCatalog([VISIBLE])
    client = _client(uow, catalog, vcs)

    first = client.get(f"/api/v1/reviews/{run.id}/diff")
    second = client.get(f"/api/v1/reviews/{run.id}/diff")

    assert first.status_code == second.status_code == 200
    assert first.json() == second.json()
    assert len(_diff_calls(vcs)) == 1
    assert catalog.calls.count("repository_installation_id") == 1


def test_another_user_who_sees_the_repository_gets_the_cached_diff() -> None:
    """Сценарий «Другой пользователь»: видимость — его, дифф — из кэша, без запроса к GitHub."""
    uow = FakeUnitOfWork()
    run = _add_run(uow, base_sha=BASE_SHA)
    vcs = FakeVcs(diff=RUN_DIFF)
    catalog = FakeInstallationCatalog([], repositories_by_token={
        EXPIRING_GRANT.access_token.reveal(): [VISIBLE],
        OTHER_TOKEN: [VISIBLE],
    })
    client = _client(uow, catalog, vcs)
    first = client.get(f"/api/v1/reviews/{run.id}/diff")

    _second_user(client, uow)
    second = client.get(f"/api/v1/reviews/{run.id}/diff")

    # Видимость второму посчитана по его токену, а не взята из кэша первого.
    assert [token.reveal() for token in catalog.tokens] == [EXPIRING_GRANT.access_token.reveal(), OTHER_TOKEN]
    assert second.status_code == 200
    assert second.json() == first.json()
    assert len(_diff_calls(vcs)) == 1


def test_failure_is_not_remembered() -> None:
    """Сценарий «Отказ не запоминается»: после `502` следующий запрос снова идёт к GitHub."""
    uow = FakeUnitOfWork()
    run = _add_run(uow, base_sha=BASE_SHA)
    vcs = FakeVcs(diff=RUN_DIFF, error=VcsError("HTTP 502"))
    client = _client(uow, vcs=vcs)
    assert client.get(f"/api/v1/reviews/{run.id}/diff").status_code == 502

    vcs.error = None
    response = client.get(f"/api/v1/reviews/{run.id}/diff")

    assert response.status_code == 200
    assert len(_diff_calls(vcs)) == 2


def test_two_runs_of_one_repository_ask_the_installation_once_each() -> None:
    """Сценарий «Номер установки не запоминается»: два прогона одного репозитория, ни один не в
    кэше диффов — GitHub спрашивается о номере установки на каждый прогон."""
    uow = FakeUnitOfWork()
    first_run = _add_run(uow, base_sha=BASE_SHA)
    second_run = _run(merge_request_id=first_run.merge_request_id, head_sha="def456", base_sha=BASE_SHA)
    uow.review_runs.add(second_run)
    vcs = FakeVcs(diff=RUN_DIFF)
    catalog = FakeInstallationCatalog([VISIBLE])
    client = _client(uow, catalog, vcs)

    assert client.get(f"/api/v1/reviews/{first_run.id}/diff").status_code == 200
    assert client.get(f"/api/v1/reviews/{second_run.id}/diff").status_code == 200

    assert catalog.calls.count("repository_installation_id") == 2
    assert [call[3] for call in _diff_calls(vcs)] == ["abc123", "def456"]


def test_reinstalled_app_gives_a_new_run_its_diff_with_the_new_installation() -> None:
    """Регресс: номер установки не кэшируется — после переустановки приложения новый прогон
    получает дифф с новым номером, а не `502` от GitHub на старый."""
    uow = FakeUnitOfWork()
    first_run = _add_run(uow, base_sha=BASE_SHA)
    second_run = _run(merge_request_id=first_run.merge_request_id, head_sha="def456", base_sha=BASE_SHA)
    uow.review_runs.add(second_run)
    vcs = FakeVcs(diff=RUN_DIFF)
    catalog = FakeInstallationCatalog([VISIBLE], installation_ids={"octo-org/api": 77})
    client = _client(uow, catalog, vcs)
    assert client.get(f"/api/v1/reviews/{first_run.id}/diff").status_code == 200

    catalog.installation_ids["octo-org/api"] = 78
    response = client.get(f"/api/v1/reviews/{second_run.id}/diff")

    assert response.status_code == 200
    assert [(call[3], call[4]) for call in _diff_calls(vcs)] == [("abc123", 77), ("def456", 78)]


def test_not_installed_is_not_remembered() -> None:
    """Сценарий «Не установлено — не запоминается»: следующий запрос снова спрашивает установку."""
    uow = FakeUnitOfWork()
    run = _add_run(uow, base_sha=BASE_SHA)
    catalog = FakeInstallationCatalog([VISIBLE], not_installed={"octo-org/api"})
    client = _client(uow, catalog, FakeVcs(diff=RUN_DIFF))

    first = client.get(f"/api/v1/reviews/{run.id}/diff")
    second = client.get(f"/api/v1/reviews/{run.id}/diff")

    assert first.status_code == second.status_code == 404
    assert catalog.calls.count("repository_installation_id") == 2
