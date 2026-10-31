"""Check run merge gate по открытым критичным тредам."""

import logging
from uuid import UUID

from app.application.ports.unit_of_work import UnitOfWork
from app.application.ports.vcs_gateway import VcsGateway
from app.application.ports.vcs_review import MergeGateCheck
from app.domain.entities import Finding
from app.domain.enums import ReviewRunStatus
from app.domain.merge_gate import merge_blocked_by_findings, untracked_findings
from app.domain.vcs_errors import VcsError

logger = logging.getLogger(__name__)

# Поправку gate отправляет только тот, кто увидел смену прогона, и второй
# возможности у неё нет: после сбоя на head остаётся `success` по старому диффу.
# Адаптер сам повторяет 5xx и лимит; здесь - ещё попытки после его отказа.
SETTLE_ATTEMPTS = 3


def collect_open_findings(
    uow: UnitOfWork, merge_request_id: UUID, *, head_run_id: UUID | None
) -> tuple[Finding, ...]:
    """Открытые треды PR и находки прогона на head, чей id inline не получен.

    `head_run_id` - прогон, чьё ревью стоит на текущем head; `None` - такого нет.
    """
    with uow:
        threads = uow.published_comments.list_open_threads_for_merge_request(merge_request_id)
        untracked: tuple[Finding, ...] = ()
        if head_run_id is not None:
            untracked = untracked_findings(
                uow.findings.list_for_run(head_run_id),
                uow.published_comments.list_for_run(head_run_id),
            )
    return tuple(t.finding for t in threads) + untracked


# Выключенный репозиторий (`DELETE /repositories/{id}`): ревью нет, но обязательная проверка
# в ruleset без check run держала бы каждый новый PR в «Expected — Waiting for status».
DISABLED_REVIEW_CHECK = MergeGateCheck(
    conclusion="success",
    title="Ревью выключено в сервисе",
    summary="Ревью этого репозитория выключено в сервисе: проверка мёрж не блокирует.",
)


def build_merge_gate_check(open_findings: tuple[Finding, ...]) -> MergeGateCheck:
    blocked = merge_blocked_by_findings(open_findings)
    conclusion = "failure" if blocked else "success"
    title = "Есть критичные замечания" if blocked else "Ревью пройдено"
    summary = (
        "Остаются незакрытые замечания уровня critical — мердж заблокирован."
        if blocked
        else "Критичных незакрытых замечаний нет."
    )
    return MergeGateCheck(conclusion=conclusion, title=title, summary=summary)


def build_retarget_check() -> MergeGateCheck:
    """Блокировка head после смены целевой ветки PR, до ревью нового диффа (#222).

    `failure`, а не `in_progress`: если новое ревью упадёт, блокировка остаётся,
    а прежний `success` по старому диффу не возвращается.
    """
    return MergeGateCheck(
        conclusion="failure",
        title="Целевая ветка сменилась — идёт повторное ревью",
        summary="Прежнее ревью проверяло дифф против другой ветки. Мердж заблокирован до ревью нового диффа.",
    )


def build_failed_review_check() -> MergeGateCheck:
    """Блокировка head, чей последний прогон упал (ревью #225).

    Прогон мог упасть после блокировки смены ветки, а её POST - не дойти: тогда
    на head остался бы `success` прежнего прогона по старому диффу.
    """
    return MergeGateCheck(
        conclusion="failure",
        title="Ревью не удалось",
        summary="Последнее ревью этого коммита упало. Мердж заблокирован до успешного ревью.",
    )


def settle_merge_gate(
    uow: UnitOfWork,
    vcs: VcsGateway,
    *,
    merge_request_id: UUID,
    repo_full_name: str,
    head_sha: str,
    installation_id: int,
    run_id: UUID,
) -> None:
    """Поправить gate, только что выставленный по прогону `run_id`, если тот уже не
    отвечает за head (#222).

    Гонка со сменой целевой ветки: приём сначала коммитит отмену старого прогона и
    новый прогон, потом выставляет блокировку. Кто выставил gate по старому прогону,
    перечитывает последний прогон на head после своего POST: если приём уже
    закоммитил, здесь видна смена и gate переписывается; если нет - блокировка
    приёма придёт позже и перекроет `success`. Так любая перестановка кончается
    gate актуального прогона. Каждая попытка поправки заново читает последний
    прогон и собирает check; после сбоя `SETTLE_ATTEMPTS` попыток - `VcsError`
    вызывающему.
    """
    failures = 0
    # Прогон и статус, по которым выставлен последний check: итог зависит от статуса,
    # и завершение прогона после нашей блокировки требует выставить его gate.
    settled: tuple[UUID, ReviewRunStatus] | None = None
    while True:
        # Перед каждой попыткой - заново: за время сбоя за head мог начать
        # отвечать другой прогон, и повтор прежнего check вернул бы чужой итог.
        with uow:
            latest = uow.review_runs.find_latest(merge_request_id, head_sha)
        if latest is None or (latest.id == run_id and latest.status is not ReviewRunStatus.CANCELLED):
            return
        if (latest.id, latest.status) == settled:
            return
        if latest.status is ReviewRunStatus.COMPLETED:
            # Новый прогон успел завершиться: его gate мог уйти раньше нашего POST.
            check = build_merge_gate_check(
                collect_open_findings(uow, merge_request_id, head_run_id=latest.id)
            )
        elif latest.status is ReviewRunStatus.FAILED:
            # Новый прогон уже упал: повторного ревью не будет, head ждёт push.
            check = build_failed_review_check()
        else:
            check = build_retarget_check()
        logger.info(
            "merge gate %s@%s переписан: прогон %s больше не отвечает за head, последний - %s",
            repo_full_name,
            head_sha,
            run_id,
            latest.id,
        )
        try:
            vcs.set_merge_gate_check(repo_full_name, head_sha, installation_id, check)
        except VcsError:
            failures += 1
            if failures == SETTLE_ATTEMPTS:
                raise
            logger.warning(
                "поправка merge gate %s@%s не выставлена, попытка %d из %d",
                repo_full_name,
                head_sha,
                failures,
                SETTLE_ATTEMPTS,
                exc_info=True,
            )
            continue
        # Выставлен gate прогона `latest`: как и любой, кто выставил gate, после
        # POST проверяем, что за head отвечает всё ещё он.
        settled = (latest.id, latest.status)
