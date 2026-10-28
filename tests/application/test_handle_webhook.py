"""Use case приёма вебхука на фейках портов: без базы, брокера и сети.

Каждый тест сверяет исход — ignored / failure / duplicate / conflict /
success — с точными записями и задачей, а не с «чем-то добавилось».
Повторный вызов `run_case` с тем же `uow`/`queue` — это повторная доставка
тем же браузером событий.
"""

from datetime import timedelta
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

# Приём берёт только метаданные: дифф достаёт воркер с лимитом размера.
EXPECTED_VCS_CALLS = [("metadata", REPO_FULL_NAME, 6, INSTALLATION_ID)]


def run_case(
    action: str = "opened",
    *,
    vcs_error: VcsError | None = None,
    vcs_diff: str = DIFF,
    vcs_meta=None,
    check_error: VcsError | None = None,
    uow: FakeUow | None = None,
    queue: FakeQueue | None = None,
    base_ref: str | None = None,
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
        check_error=check_error,
    )
    queue = queue if queue is not None else FakeQueue()
    outcome = handle_webhook_event(
        webhook_payload(action, base_ref=base_ref),
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
    # id задачи — идентификатор сообщения, новый на каждую постановку; прогон
    # указывается review_run_id (§4.2, спека job-queue): две постановки одного
    # прогона различимы в DLQ.
    assert [job.review_run_id for job in queue.jobs] == [run.id]
    assert all(job.id != run.id for job in queue.jobs)


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
    # Инсталляция для повторного получения диффа воркером — из payload'а
    # доставленного вебхука, а не из конфигурации.
    assert job.installation_id == INSTALLATION_ID
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


def test_intake_does_not_fetch_the_diff() -> None:
    """Дифф любого размера не трогает ответ на доставку: его достаёт воркер."""
    outcome, _, vcs, queue = run_case()

    assert outcome.kind == "success"
    assert [call for call in vcs.calls if call[0] == "diff"] == []
    assert len(queue.jobs) == 1


def test_vcs_error_yields_failure_and_creates_nothing() -> None:
    outcome, uow, vcs, queue = run_case(vcs_error=VcsError("GitHub API: HTTP 502"))
    assert outcome.kind == "failure"
    # Метаданные запросили — и на ошибке VCS обработка прекратилась.
    assert vcs.calls == EXPECTED_VCS_CALLS
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


def test_redelivery_after_a_finished_run_is_a_duplicate() -> None:
    """#75: повтор после завершения не даёт второго прогона и второго ревью.

    GitHub переотправляет доставку с тем же `X-GitHub-Delivery`, а запуск
    ревью — только opened и synchronize: тот же коммит ещё раз — повтор.
    """
    _, uow, _, queue = run_case()
    (finished,) = uow.review_runs.added
    (original_mr,) = uow.merge_requests.added
    object.__setattr__(finished, "status", ReviewRunStatus.COMPLETED)

    outcome, uow, _, queue = run_case(uow=uow, queue=queue)
    assert outcome.kind == "duplicate"
    assert outcome.run is finished
    assert outcome.merge_request is original_mr
    assert uow.merge_requests.updated == []
    assert len(uow.review_runs.added) == 1
    assert len(queue.jobs) == 1


def test_failed_run_on_same_commit_does_not_block_new_one() -> None:
    """`failed` дублем не делает: повтор после сбоя должен дать ревью.

    Фейк фильтрует так же, как `SqlAlchemyReviewRunRepo.find_reviewed`, поэтому
    выдёргивание фильтра из продакшн-запроса красит этот тест.
    """
    _, uow, _, queue = run_case()
    (finished,) = uow.review_runs.added
    object.__setattr__(finished, "status", ReviewRunStatus.FAILED)

    outcome, uow, _, queue = run_case(uow=uow, queue=queue)
    assert outcome.kind == "success"
    assert len(uow.review_runs.added) == 2
    assert len(queue.jobs) == 2


def test_finished_run_of_another_trigger_does_not_make_a_duplicate() -> None:
    """Дублем делает только прогон по вебхуку: явное повторное ревью — не доставка."""
    _, uow, _, queue = run_case()
    (finished,) = uow.review_runs.added
    object.__setattr__(finished, "status", ReviewRunStatus.COMPLETED)
    object.__setattr__(finished, "trigger", TriggerSource.MANUAL)

    outcome, uow, _, queue = run_case(uow=uow, queue=queue)
    assert outcome.kind == "success"
    assert len(uow.review_runs.added) == 2


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


def test_race_with_a_different_commit_retries_the_write() -> None:
    """Гонка первых доставок с разными коммитами (opened и synchronize): у
    победителя нет активного прогона на коммит проигравшего, есть только
    merge_request — запись повторяется поверх его записей, а не отвечает 5xx.
    """
    outcome, uow, _, queue = run_case(action="opened")
    assert outcome.kind == "success"

    uow.commit_errors.append(ActiveRunConflict("проигравший с другим коммитом"))
    outcome, uow, _, queue = run_case(action="synchronize", uow=uow, queue=queue)
    assert outcome.kind == "success"
    mr = merge_request_of(uow)
    assert mr.head_sha == "0987654321abcdef0987654321abcdef09876543"
    (new_run,) = uow.review_runs.added[1:]
    assert new_run.head_sha == mr.head_sha
    assert queue.jobs[-1].review_run_id == new_run.id
    assert queue.jobs[-1].action == "synchronize"


def test_broker_failure_fails_the_run_and_next_delivery_starts_a_new_one() -> None:
    """Сбой брокера после коммита компенсируется сразу, как `_fail_unenqueued`
    в #36: прогон переводится в failed, ошибка идёт дальше — вебхук отвечает
    5xx. GitHub упавшую доставку сам не повторяет — её переотправляют вручную
    или задача #149. Оставленный queued блокировал бы коммит
    от ревью навсегда: повторная доставка до sweep получала бы duplicate и
    задачу больше не ставила."""

    class BrokenQueue(FakeQueue):
        def enqueue(self, job) -> None:
            raise RuntimeError("брокер недоступен")

    uow = FakeUow()
    with pytest.raises(RuntimeError, match="брокер недоступен"):
        run_case(uow=uow, queue=BrokenQueue())
    assert uow.commits == 2  # прогон и его компенсация
    (failed,) = uow.review_runs.runs
    assert failed.status is ReviewRunStatus.FAILED
    assert failed.failure_reason is not None
    assert "брокер недоступен" in failed.failure_reason

    # failed терминален: повторная доставка создаёт новый прогон и задачу.
    outcome, uow, _, queue = run_case(uow=uow, queue=FakeQueue())
    assert outcome.kind == "success"
    assert len(uow.review_runs.added) == 2
    assert len(queue.jobs) == 1


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


# --- #222: смена целевой ветки -------------------------------------------------

OLD_BASE = "0" * 40
NEW_BASE = "1" * 40
OLD_TARGET = pr_metadata(OLD_BASE, "main")
NEW_TARGET = pr_metadata(NEW_BASE, "develop")


def reviewed_on_old_target() -> tuple[FakeUow, FakeQueue, Any]:
    _, uow, _, queue = run_case(vcs_meta=OLD_TARGET)
    (reviewed,) = uow.review_runs.added
    object.__setattr__(reviewed, "status", ReviewRunStatus.COMPLETED)
    # Старше прогонов, которые тест создаст следом в `NOW`: «последний» - по времени.
    object.__setattr__(reviewed, "created_at", NOW - timedelta(minutes=1))
    return uow, queue, reviewed


def test_retarget_after_a_completed_review_starts_a_review_of_the_new_diff() -> None:
    """Обход merge gate: ревью на старой ветке не делает дублем смену ветки."""
    uow, queue, _ = reviewed_on_old_target()

    outcome, uow, _, queue = run_case("edited", vcs_meta=NEW_TARGET, uow=uow, queue=queue)

    assert outcome.kind == "success"
    assert outcome.run is not None and outcome.run.base_sha == NEW_BASE
    assert merge_request_of(uow).target_branch == "develop"
    assert [job.base_sha for job in queue.jobs] == [OLD_BASE, NEW_BASE]
    assert [job.action for job in queue.jobs] == ["opened", "edited"]


def test_retarget_during_an_active_review_cancels_it_and_starts_a_new_one() -> None:
    """Прогон на старой ветке ещё идёт: он отменяется, новый ставится в очередь -
    иначе он выставил бы merge gate по диффу, которого в PR больше нет."""
    _, uow, _, queue = run_case(vcs_meta=OLD_TARGET)
    (stale,) = uow.review_runs.added

    outcome, uow, _, queue = run_case("edited", vcs_meta=NEW_TARGET, uow=uow, queue=queue)

    assert outcome.kind == "success"
    cancelled = uow.review_runs.get(stale.id)
    assert cancelled is not None and cancelled.status is ReviewRunStatus.CANCELLED
    assert outcome.run is not None and outcome.run.status is ReviewRunStatus.QUEUED
    assert len(queue.jobs) == 2


def test_redelivery_of_the_retarget_is_a_duplicate() -> None:
    uow, queue, _ = reviewed_on_old_target()
    run_case("edited", vcs_meta=NEW_TARGET, uow=uow, queue=queue)

    outcome, uow, _, queue = run_case("edited", vcs_meta=NEW_TARGET, uow=uow, queue=queue)

    assert outcome.kind == "duplicate"
    assert len(queue.jobs) == 2


def test_a_stale_retarget_event_does_not_retarget() -> None:
    """Ревью #225: ветку сменили на `develop` и тут же вернули на `main`. Запоздавшее
    `edited` на `develop` получило свежие метаданные уже с `main` - ретаргета нет,
    иначе прогон ревьюил бы дифф против ветки, которой у PR уже нет."""
    uow, queue, reviewed = reviewed_on_old_target()

    outcome, uow, vcs, queue = run_case(
        "edited", vcs_meta=pr_metadata(NEW_BASE, "main"), uow=uow, queue=queue, base_ref="develop"
    )

    assert outcome.kind == "duplicate"
    assert outcome.run is reviewed
    assert merge_request_of(uow).target_branch == "main"
    assert vcs.checks == []
    assert len(queue.jobs) == 1


def test_redelivery_after_the_target_branch_moved_is_a_duplicate() -> None:
    """Вершина целевой ветки ушла вперёд (влили чужой PR), ветка та же: повтор
    доставки того же head - дубль, а не «целевая ветка сменилась» с новым прогоном."""
    uow, queue, reviewed = reviewed_on_old_target()

    outcome, uow, vcs, queue = run_case(
        vcs_meta=pr_metadata(NEW_BASE, "main"), uow=uow, queue=queue
    )

    assert outcome.kind == "duplicate"
    assert outcome.run is reviewed
    assert vcs.checks == []
    assert len(queue.jobs) == 1


def test_edited_back_after_a_failed_review_of_the_new_target_reviews_again() -> None:
    """A -> B, ревью B упало, B -> A: прогон на A старше упавшего - head не
    проверен, нужен новый прогон с блокировкой, а не дубль прогона на A."""
    uow, queue, _ = reviewed_on_old_target()
    outcome, uow, _, queue = run_case("edited", vcs_meta=NEW_TARGET, uow=uow, queue=queue)
    assert outcome.run is not None
    object.__setattr__(outcome.run, "status", ReviewRunStatus.FAILED)

    outcome, uow, vcs, queue = run_case(
        "edited", vcs_meta=OLD_TARGET, uow=uow, queue=queue, base_ref="main"
    )

    assert outcome.kind == "success"
    assert [check.conclusion for _, check in vcs.checks] == ["failure"]
    assert len(queue.jobs) == 3


def test_redelivery_after_a_failed_review_of_the_new_target_reviews_again() -> None:
    """Последний прогон на head упал после смены ветки: повтор доставки не
    дубль более старого `completed` на прежней ветке."""
    uow, queue, _ = reviewed_on_old_target()
    outcome, uow, _, queue = run_case("edited", vcs_meta=NEW_TARGET, uow=uow, queue=queue)
    assert outcome.run is not None
    object.__setattr__(outcome.run, "status", ReviewRunStatus.FAILED)

    outcome, uow, _, queue = run_case(vcs_meta=NEW_TARGET, uow=uow, queue=queue)

    assert outcome.kind == "success"
    assert len(queue.jobs) == 3


def test_cancelled_latest_run_is_not_a_duplicate() -> None:
    """Отменён - значит, его ревью заменили: если замены нет, повтор доставки ревьюит."""
    _, uow, _, queue = run_case()
    (cancelled,) = uow.review_runs.added
    object.__setattr__(cancelled, "status", ReviewRunStatus.CANCELLED)

    outcome, uow, _, queue = run_case(uow=uow, queue=queue)

    assert outcome.kind == "success"
    assert len(queue.jobs) == 2


def test_retarget_blocks_the_merge_gate_before_the_job_is_enqueued() -> None:
    """Пока новый дифф не проверен, на head висит блокирующий check, а не прежний
    `success`; блокировка - после коммита нового прогона и до задачи."""
    uow, queue, _ = reviewed_on_old_target()
    order: list[str] = []
    commit = uow.commit

    def tracking_commit() -> None:
        order.append("commit")
        commit()

    uow.commit = tracking_commit  # type: ignore[method-assign]
    vcs = FakeVcs(diff=DIFF, meta=NEW_TARGET)
    record_check = vcs.set_merge_gate_check
    enqueue = queue.enqueue

    def tracking_check(*args: Any) -> None:
        order.append("check")
        record_check(*args)

    def tracking_enqueue(job: Any) -> None:
        order.append("enqueue")
        enqueue(job)

    vcs.set_merge_gate_check = tracking_check  # type: ignore[method-assign,assignment]
    queue.enqueue = tracking_enqueue  # type: ignore[method-assign]
    outcome = handle_webhook_event(
        webhook_payload("edited"), uow, vcs, queue, now=lambda: NOW, new_id=uuid4
    )

    assert outcome.kind == "success"
    assert outcome.run is not None
    ((head_sha, check),) = vcs.checks
    assert head_sha == outcome.run.head_sha
    assert check.conclusion == "failure"
    assert order == ["commit", "check", "enqueue"]


def test_retarget_whose_gate_cannot_be_blocked_is_rolled_back() -> None:
    """GitHub недоступен на блокировке gate: 502, задачи нет, новый прогон - в
    `failed`, PR - на прежней ветке. Повтор доставки (#149) снова видит смену
    ветки и блокирует head, а не ревьюит под прежним `success`."""
    uow, queue, _ = reviewed_on_old_target()

    outcome, uow, _, queue = run_case(
        "edited", vcs_meta=NEW_TARGET, uow=uow, queue=queue, check_error=VcsError("502")
    )

    assert outcome.kind == "failure"
    assert len(queue.jobs) == 1
    (_, attempted) = uow.review_runs.added
    failed = uow.review_runs.get(attempted.id)
    assert failed is not None and failed.status is ReviewRunStatus.FAILED
    assert merge_request_of(uow).target_branch == "main"

    outcome, uow, vcs, queue = run_case("edited", vcs_meta=NEW_TARGET, uow=uow, queue=queue)

    assert outcome.kind == "success"
    assert [check.conclusion for _, check in vcs.checks] == ["failure"]
    assert len(queue.jobs) == 2


def test_first_review_and_redelivery_touch_no_gate() -> None:
    _, uow, vcs, queue = run_case()
    assert vcs.checks == []
    _, uow, vcs, queue = run_case(uow=uow, queue=queue)
    assert vcs.checks == []
