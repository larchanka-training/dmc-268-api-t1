"""Use case приёма вебхука на фейках портов: без базы, брокера и сети.

Каждый тест сверяет исход — ignored / failure / duplicate / conflict /
success — с точными записями и задачей, а не с «чем-то добавилось».
Повторный вызов `run_case` с тем же `uow`/`queue` — это повторная доставка
тем же браузером событий.
"""

from typing import Any
from uuid import uuid4

import pytest

from app.application.ports.unit_of_work import ActiveRunConflict
from app.application.use_cases.handle_webhook import (
    WebhookOutcome,
    handle_webhook_event,
)
from app.domain.enums import Provider, ReviewRunStatus, TriggerSource
from app.domain.vcs_errors import VcsError

from ..fakes import (
    DIFF,
    INSTALLATION_ID,
    NOW,
    REGISTERED_PROVIDER_ID,
    REPO_FULL_NAME,
    FakeQueue,
    FakeUow,
    FakeVcs,
    a_repository,
    pr_metadata,
    webhook_payload,
)

EXPECTED_VCS_CALLS = [
    ("diff", REPO_FULL_NAME, 6, INSTALLATION_ID),
    ("metadata", REPO_FULL_NAME, 6, INSTALLATION_ID),
]


def run_case(
    action: str = "opened",
    *,
    vcs_error: VcsError | None = None,
    vcs_diff: str = DIFF,
    vcs_meta=None,
    uow: FakeUow | None = None,
    queue: FakeQueue | None = None,
) -> tuple[WebhookOutcome, FakeUow, FakeVcs, FakeQueue]:
    uow = uow if uow is not None else FakeUow()
    # Повторный вызов с тем же uow — это повторная доставка: репозиторий
    # регистрируется один раз, второй add перезаписал бы его новым id.
    if (Provider.GITHUB, REGISTERED_PROVIDER_ID) not in uow.repositories.stored:
        uow.repositories.add(a_repository())
    vcs = FakeVcs(
        diff=vcs_diff,
        meta=vcs_meta if vcs_meta is not None else pr_metadata(),
        error=vcs_error,
    )
    queue = queue if queue is not None else FakeQueue()
    outcome = handle_webhook_event(
        webhook_payload(action),
        uow,
        vcs,
        queue,
        now=lambda: NOW,
        new_id=uuid4,
    )
    return outcome, uow, vcs, queue


def merge_request_of(uow: FakeUow) -> Any:
    repo = uow.repositories.stored[(Provider.GITHUB, REGISTERED_PROVIDER_ID)]
    return uow.merge_requests.by_number[(repo.id, 6)]


def test_opened_creates_merge_request_run_and_job() -> None:
    outcome, uow, vcs, queue = run_case()
    (run,) = uow.review_runs.added
    mr = merge_request_of(uow)
    assert outcome.kind == "success"
    # Сравнение по значению, а не через `is`: identity-ассерт с Any-правой
    # стороной сужает тип переменной до Optional и ломает проверки ниже.
    assert outcome.run == run
    assert outcome.merge_request == mr
    assert vcs.calls == EXPECTED_VCS_CALLS
    assert len(uow.merge_requests.added) == 1
    # Заголовок, автор и ветки — из свежих метаданных, head — из доставки.
    assert (mr.title, mr.author) == (pr_metadata().title, pr_metadata().author)
    assert (mr.source_branch, mr.target_branch) == (
        pr_metadata().source_branch,
        pr_metadata().target_branch,
    )
    assert mr.head_sha == "a1b2c3d4e5f6789012345678abcdef0123456789"
    assert run.merge_request_id == mr.id
    assert run.head_sha == mr.head_sha
    assert run.status is ReviewRunStatus.QUEUED
    assert run.trigger is TriggerSource.WEBHOOK
    assert run.base_sha == pr_metadata().base_sha
    assert (run.created_at, run.last_progress_at) == (NOW, NOW)
    assert uow.commits == 1
    # id задачи — id прогона: очередь и таблица указывают на одну попытку.
    assert [job.id for job in queue.jobs] == [run.id]


def test_opened_job_message_carries_section_4_2_fields() -> None:
    _, _, _, queue = run_case()
    (job,) = queue.jobs
    assert job.repository_full_name == REPO_FULL_NAME
    assert job.repository_provider_id == REGISTERED_PROVIDER_ID
    assert job.pull_request_number == 6
    assert job.head_sha == "a1b2c3d4e5f6789012345678abcdef0123456789"
    assert job.base_sha == pr_metadata().base_sha
    assert job.action == "opened"
    assert job.event_type == "pull_request"
    # Приоритета в сообщении нет: это метаданное доставки, его выводит
    # адаптер очереди — слой приложения про тарифы знать не должен.
    assert not hasattr(job, "priority")


def test_base_sha_comes_from_metadata_not_from_payload() -> None:
    """D6: base.sha в payload может быть устаревшим — берём из свежих метаданных."""
    fresh_base = "0000fresh0000fresh0000fresh0000fresh0000f"
    _, _, _, queue = run_case(vcs_meta=pr_metadata(base_sha=fresh_base))
    (job,) = queue.jobs
    assert job.base_sha == fresh_base
    assert job.base_sha != webhook_payload()["pull_request"]["base"]["sha"]


def test_merge_request_title_comes_from_metadata_not_from_payload() -> None:
    """Метаданные тем же запросом, что и base_sha: заголовок в базе — их."""
    _, uow, _, _ = run_case()
    mr = merge_request_of(uow)
    assert mr.title != webhook_payload()["pull_request"]["title"]
    assert mr.title == pr_metadata().title


def test_synchronize_shifts_head_sha_of_existing_merge_request() -> None:
    outcome, uow, _, queue = run_case(action="opened")
    (first_run,) = uow.review_runs.added

    outcome, uow, _, queue = run_case(action="synchronize", uow=uow, queue=queue)
    assert outcome.kind == "success"
    mr = merge_request_of(uow)
    assert mr.head_sha == "0987654321abcdef0987654321abcdef09876543"
    assert len(uow.merge_requests.updated) == 1
    new_run = uow.review_runs.added[-1]
    assert new_run.id != first_run.id
    assert new_run.head_sha == mr.head_sha
    assert queue.jobs[-1].head_sha == new_run.head_sha
    assert queue.jobs[-1].action == "synchronize"


def test_reopened_is_ignored_without_any_records() -> None:
    outcome, uow, vcs, queue = run_case(action="reopened")
    assert outcome.kind == "ignored"
    assert vcs.calls == []
    assert uow.merge_requests.added == []
    assert uow.review_runs.added == []
    assert queue.jobs == []
    assert uow.commits == 0


@pytest.mark.parametrize("action", ["closed", "assigned", "ready_for_review"])
def test_unknown_actions_are_ignored(action: str) -> None:
    outcome, uow, _, queue = run_case(action=action)
    assert outcome.kind == "ignored"
    assert uow.commits == 0
    assert queue.jobs == []


def test_unregistered_repository_is_ignored_without_records() -> None:
    """D5: регистрации на лету нет — вебхук чужого репозитория это пустой игнор."""
    uow = FakeUow()  # ни одного репозитория не зарегистрировано
    vcs = FakeVcs()
    queue = FakeQueue()
    outcome = handle_webhook_event(
        webhook_payload(), uow, vcs, queue, now=lambda: NOW, new_id=uuid4
    )
    assert outcome.kind == "ignored"
    assert vcs.calls == []
    assert uow.merge_requests.added == []
    assert uow.review_runs.added == []
    assert queue.jobs == []


def test_vcs_error_yields_failure_and_creates_nothing() -> None:
    outcome, uow, vcs, queue = run_case(vcs_error=VcsError("GitHub API: HTTP 502"))
    assert outcome.kind == "failure"
    # Дифф запросили — и на его ошибке обработка прекратилась: метаданные
    # за ним не запрашиваются.
    assert vcs.calls == [EXPECTED_VCS_CALLS[0]]
    assert uow.merge_requests.added == []
    assert uow.review_runs.added == []
    assert queue.jobs == []
    assert uow.commits == 0


def test_duplicate_delivery_returns_existing_run_and_enqueues_nothing() -> None:
    outcome, uow, _, queue = run_case()
    (existing,) = uow.review_runs.added
    (original_mr,) = uow.merge_requests.added

    outcome, uow, _, queue = run_case(uow=uow, queue=queue)
    assert outcome.kind == "duplicate"
    assert outcome.run is existing
    # Дубль не трогает merge_request: на PR один активный прогон, а
    # обновление без коммита откатилось бы молча при выходе из транзакции.
    assert uow.merge_requests.updated == []
    assert outcome.merge_request is original_mr
    assert len(uow.review_runs.added) == 1
    assert len(queue.jobs) == 1


def test_terminal_run_on_same_commit_does_not_block_new_one() -> None:
    """Завершённый прогон неактивен: `find_active` его пропускает.

    Фейк фильтрует так же, как `SqlAlchemyReviewRunRepo.find_active`, поэтому
    выдёргивание фильтра из продакшн-запроса красит этот тест.
    """
    _, uow, _, queue = run_case()
    (finished,) = uow.review_runs.added
    object.__setattr__(finished, "status", ReviewRunStatus.COMPLETED)

    outcome, uow, _, queue = run_case(uow=uow, queue=queue)
    assert outcome.kind == "success"
    assert len(uow.review_runs.added) == 2
    assert len(queue.jobs) == 2


def test_lost_race_returns_conflict_with_survivor_run(monkeypatch) -> None:
    """Гонка: pre-check победителя не видел, коммит упал на индексе."""
    _, uow, _, queue = run_case()
    (winner,) = uow.review_runs.added

    original_find_active = uow.review_runs.find_active
    seen = {"n": 0}

    def racing(merge_request_id, head_sha):
        seen["n"] += 1
        if seen["n"] == 1:
            return None  # в окне гонки победитель ещё не виден
        return original_find_active(merge_request_id, head_sha)

    monkeypatch.setattr(uow.review_runs, "find_active", racing)
    uow.commit_error = ActiveRunConflict("проигравший гонку")

    outcome, uow, _, queue = run_case(uow=uow, queue=queue)
    assert outcome.kind == "conflict"
    assert outcome.run is winner
    assert uow.rollbacks >= 1


def test_lost_race_without_survivor_reraises() -> None:
    """Отказ коммита без выжившего (чужое ограничение) не глотается."""
    uow = FakeUow()
    uow.commit_error = ActiveRunConflict("нет выжившего")
    with pytest.raises(ActiveRunConflict):
        run_case(uow=uow)


def test_broker_failure_leaves_committed_run_for_the_sweep() -> None:
    """Сбой брокера после коммита: прогон остаётся закоммиченным queued
    без сообщения — порядок коммит → задача выбран по воркеру #36, который
    сообщение без строки кладёт в DLQ. Сироту переводит в `failed` sweep
    обработки (`list_unfinished`); до того повторная доставка находит
    активный прогон и отвечает `duplicate`, не плодя второй."""

    class BrokenQueue(FakeQueue):
        def enqueue(self, job) -> None:
            raise RuntimeError("брокер недоступен")

    uow = FakeUow()
    with pytest.raises(RuntimeError, match="брокер недоступен"):
        run_case(uow=uow, queue=BrokenQueue())
    assert uow.commits == 1  # прогон закоммичен...
    (orphan,) = uow.review_runs.runs
    assert orphan.status is ReviewRunStatus.QUEUED

    # Повторная доставка того же коммита находит сироту: дубль, без второго
    # прогона и без второй задачи.
    outcome, uow, _, queue = run_case(uow=uow, queue=FakeQueue())
    assert outcome.kind == "duplicate"
    assert outcome.run is orphan
    assert len(uow.review_runs.added) == 1
    assert queue.jobs == []  # дубль задачу не ставит


def test_new_head_after_active_run_starts_new_run() -> None:
    """Активный прогон на старом коммите не блокирует новый на новом коммите."""
    _, uow, _, queue = run_case(action="opened")
    (old_run,) = uow.review_runs.added
    assert old_run.head_sha != "0987654321abcdef0987654321abcdef09876543"

    outcome, uow, _, queue = run_case(action="synchronize", uow=uow, queue=queue)
    assert outcome.kind == "success"
    assert len(uow.review_runs.added) == 2
    assert old_run.head_sha == "a1b2c3d4e5f6789012345678abcdef0123456789"


def test_diff_is_never_persisted() -> None:
    """D3: дифф транзиентен — ни MR, ни прогон, ни задача его не содержат."""
    lock_diff = (
        "diff --git a/package-lock.json b/package-lock.json\n"
        "--- a/package-lock.json\n+++ b/package-lock.json\n"
        "@@ -1 +1 @@\n-old\n+new\n"
    )
    outcome, uow, _, queue = run_case(vcs_diff=lock_diff)
    assert outcome.kind == "success"
    mr = merge_request_of(uow)
    (run,) = uow.review_runs.added
    (job,) = queue.jobs
    for stored in (mr, run, job):
        assert "package-lock.json" not in str(stored)
