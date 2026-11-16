"""Демо-данные для демо-режима (change `demo-mode`): что записать и как записать идемпотентно.

`demo_dataset` — чистая функция: id постоянные, время отсчитывается от `now`, которое приходит
аргументом, поэтому повтор даёт те же ключи, а тест сверяет точные значения. Время — от
`now`, а не константы: аналитика кабинета по умолчанию смотрит последние дни, и данные из
прошлого года она бы не показала. `base_sha` у прогонов пуст: дифф демо-прогона кабинет
не строит (`409 DIFF_UNAVAILABLE`), а не идёт за ним к GitHub, где демо-репозитория нет.

`seed_demo` пишет набор через `UnitOfWork`, пропуская уже записанное по постоянным id:
повторный запуск ничего не дублирует и не трогает чужие данные. Уже записанные строки не
обновляются — время демо-прогонов остаётся тем, что было при первом запуске.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from uuid import UUID

from app.application.ports.unit_of_work import UnitOfWork
from app.domain.demo import DEMO_PROVIDER_USER_ID, DEMO_REPOSITORY_PREFIX
from app.domain.entities import (
    BalanceCredit,
    DiffAnchor,
    Finding,
    MergeRequest,
    Repository,
    ReviewRun,
    ReviewRunStep,
    User,
)
from app.domain.enums import (
    DiffSide,
    FindingCategory,
    FindingSeverity,
    MergeRequestState,
    Provider,
    ReviewRunStage,
    ReviewRunStatus,
    ReviewRunStepStatus,
    TriggerSource,
    VerificationOutcome,
)

DEMO_USER_ID = UUID("00000000-0000-7000-8000-00000000d001")
DEMO_MODEL = "stub"


def _id(kind: int, n: int) -> UUID:
    """Постоянный id демо-строки: вид строки и её номер — в хвосте UUID."""
    return UUID(f"00000000-0000-7000-8000-0000d{kind:03x}{n:04x}")


@dataclass(frozen=True, slots=True)
class DemoDataset:
    """Всё, что пишет скрипт начальных данных, в порядке внешних ключей."""

    user: User
    credit: BalanceCredit
    repositories: tuple[Repository, ...]
    merge_requests: tuple[MergeRequest, ...]
    runs: tuple[ReviewRun, ...]
    steps: tuple[ReviewRunStep, ...]
    findings: tuple[Finding, ...]


@dataclass(frozen=True, slots=True)
class SeedOutcome:
    """Сколько строк записано этим запуском; при повторе — нули."""

    repositories: int
    runs: int


def demo_dataset(now: datetime) -> DemoDataset:
    """Демо-пользователь, два репозитория, три PR и прогоны в статусах `completed`, `failed`, `analysing`."""
    created = now - timedelta(days=3)
    user = User(
        id=DEMO_USER_ID,
        provider=Provider.GITHUB,
        provider_user_id=DEMO_PROVIDER_USER_ID,
        login="demo",
        name="Демо-пользователь",
        avatar_url=None,
        created_at=created,
        updated_at=created,
    )
    credit = BalanceCredit(
        id=_id(1, 1),
        user_id=DEMO_USER_ID,
        amount_eur=Decimal(10),
        note="стартовый баланс демо-режима",
        credited_by="seed_demo",
        created_at=created,
    )
    api, web = (
        Repository(
            id=_id(2, n),
            provider=Provider.GITHUB,
            provider_id=f"{DEMO_REPOSITORY_PREFIX}{n}",
            full_name=f"demo-org/{name}",
            default_branch="main",
            auto_review_enabled=True,
            created_at=created,
            updated_at=created,
            connected_at=created,
            payer_user_id=DEMO_USER_ID,
        )
        for n, name in ((1, "payments-api"), (2, "web-cabinet"))
    )
    merge_requests = (
        _merge_request(1, api, 12, "Проверка подписи вебхука Stripe", "feat/stripe-signature", created),
        _merge_request(2, api, 13, "Пагинация списка платежей", "feat/payments-pagination", created),
        _merge_request(3, web, 7, "Форма входа без перезагрузки страницы", "feat/login-form", created),
    )
    completed, failed, analysing = (
        ReviewRun(
            id=_id(4, 1),
            merge_request_id=merge_requests[0].id,
            head_sha="a1" * 20,
            status=ReviewRunStatus.COMPLETED,
            trigger=TriggerSource.WEBHOOK,
            last_progress_at=now - timedelta(days=2) + timedelta(seconds=41),
            created_at=now - timedelta(days=2),
            updated_at=now - timedelta(days=2) + timedelta(seconds=41),
            model=DEMO_MODEL,
            tokens_used=5_210,
            duration_seconds=41.0,
            rejected_findings=1,
            verification=VerificationOutcome.VERIFIED,
            verifier_dropped_findings=1,
        ),
        ReviewRun(
            id=_id(4, 2),
            merge_request_id=merge_requests[1].id,
            head_sha="a2" * 20,
            status=ReviewRunStatus.FAILED,
            trigger=TriggerSource.MANUAL,
            last_progress_at=now - timedelta(days=1) + timedelta(seconds=9),
            created_at=now - timedelta(days=1),
            updated_at=now - timedelta(days=1) + timedelta(seconds=9),
            failure_reason="Модель не ответила за отведённое время.",
            failed_stage=ReviewRunStatus.ANALYSING,
            model=DEMO_MODEL,
            duration_seconds=9.0,
        ),
        # Прогон «в работе»: очистка воркера (`review.sweep`) переведёт его в `failed`, когда
        # он устареет, — в демо это тоже показательно, а повторный seed его не восстанавливает.
        ReviewRun(
            id=_id(4, 3),
            merge_request_id=merge_requests[2].id,
            head_sha="a3" * 20,
            status=ReviewRunStatus.ANALYSING,
            trigger=TriggerSource.WEBHOOK,
            last_progress_at=now,
            created_at=now - timedelta(seconds=20),
            updated_at=now,
            model=DEMO_MODEL,
        ),
    )
    steps = (
        _step(1, completed, ReviewRunStage.FETCHING_DIFF, ReviewRunStepStatus.COMPLETED, 0, 1.2),
        _step(2, completed, ReviewRunStage.BUILDING_CONTEXT, ReviewRunStepStatus.COMPLETED, 2, 3.4),
        _step(3, completed, ReviewRunStage.ANALYSING, ReviewRunStepStatus.COMPLETED, 6, 28.0, tokens=4_100),
        _step(4, completed, ReviewRunStage.VERIFYING, ReviewRunStepStatus.COMPLETED, 34, 6.1, tokens=1_110),
        _step(5, completed, ReviewRunStage.PUBLISHING, ReviewRunStepStatus.COMPLETED, 40, 0.8),
        _step(6, failed, ReviewRunStage.FETCHING_DIFF, ReviewRunStepStatus.COMPLETED, 0, 1.0),
        _step(7, failed, ReviewRunStage.BUILDING_CONTEXT, ReviewRunStepStatus.COMPLETED, 1, 2.0),
        _step(8, failed, ReviewRunStage.ANALYSING, ReviewRunStepStatus.FAILED, 3, 6.0, error="LLM_TIMEOUT"),
        _step(9, analysing, ReviewRunStage.FETCHING_DIFF, ReviewRunStepStatus.COMPLETED, 0, 1.1),
        _step(10, analysing, ReviewRunStage.BUILDING_CONTEXT, ReviewRunStepStatus.COMPLETED, 2, 2.9),
        _step(11, analysing, ReviewRunStage.ANALYSING, ReviewRunStepStatus.RUNNING, 5, None),
    )
    findings = (
        _finding(
            1,
            completed,
            "app/webhooks/stripe.py",
            42,
            FindingCategory.SECURITY,
            FindingSeverity.CRITICAL,
            "Подпись вебхука сравнивается через `==`: время сравнения выдаёт совпавший префикс. "
            "Нужно `hmac.compare_digest`.",
            "if not hmac.compare_digest(expected, received):",
        ),
        _finding(
            2,
            completed,
            "app/webhooks/stripe.py",
            58,
            FindingCategory.CORRECTNESS,
            FindingSeverity.HIGH,
            "Повторная доставка того же события зачисляет баланс второй раз: нет проверки id события.",
            None,
        ),
        _finding(
            3,
            completed,
            "app/payments/list.py",
            17,
            FindingCategory.PERFORMANCE,
            FindingSeverity.MEDIUM,
            "Запрос в цикле по платежам: N+1 к базе на странице списка.",
            None,
        ),
        _finding(
            4,
            completed,
            "app/payments/list.py",
            30,
            FindingCategory.READABILITY,
            FindingSeverity.LOW,
            "Имя `tmp` не говорит, что в переменной: лучше `pending_payments`.",
            "pending_payments = load_pending(account_id)",
        ),
    )
    return DemoDataset(
        user=user,
        credit=credit,
        repositories=(api, web),
        merge_requests=merge_requests,
        runs=(completed, failed, analysing),
        steps=steps,
        findings=findings,
    )


def seed_demo(uow: UnitOfWork, dataset: DemoDataset) -> SeedOutcome:
    """Записать демо-набор, пропуская строки, что уже есть.

    По транзакции на уровень внешних ключей: у моделей нет связей ORM между таблицами, и в
    одном flush порядок вставки разных таблиц не гарантирован — строка могла бы уйти раньше
    той, на которую ссылается. Упавший посередине запуск повтор дописывает: каждый уровень
    проверяет свои строки сам. Зачисление — только вместе с новым пользователем: журнал
    баланса идемпотентен лишь по сессии Stripe.
    """
    with uow:
        new_user = uow.users.add(dataset.user)
        uow.commit()
    repositories = 0
    with uow:
        if new_user:
            uow.billing.add_credit(dataset.credit)
        for repository in dataset.repositories:
            if uow.repositories.get(repository.id) is None and uow.repositories.add(repository):
                repositories += 1
        uow.commit()
    with uow:
        for merge_request in dataset.merge_requests:
            if uow.merge_requests.get(merge_request.id) is None:
                uow.merge_requests.add(merge_request)
        uow.commit()
    runs = 0
    with uow:
        for run in dataset.runs:
            if uow.review_runs.get(run.id) is None:
                uow.review_runs.add(run)
                runs += 1
        uow.commit()
    with uow:
        for run in dataset.runs:
            if not uow.review_run_steps.list_for_run(run.id):
                for step in (s for s in dataset.steps if s.review_run_id == run.id):
                    uow.review_run_steps.add(step)
            if not uow.findings.list_for_run(run.id):
                for finding in (f for f in dataset.findings if f.review_run_id == run.id):
                    uow.findings.add(finding)
        uow.commit()
    return SeedOutcome(repositories=repositories, runs=runs)


def _merge_request(
    n: int, repository: Repository, number: int, title: str, branch: str, created: datetime
) -> MergeRequest:
    return MergeRequest(
        id=_id(3, n),
        repository_id=repository.id,
        number=number,
        title=title,
        description="Демо-запрос на изменение из скрипта начальных данных.",
        author="demo",
        source_branch=branch,
        target_branch=repository.default_branch,
        head_sha=f"a{n}" * 20,
        state=MergeRequestState.OPEN,
        created_at=created,
        updated_at=created,
    )


def _step(
    n: int,
    run: ReviewRun,
    stage: ReviewRunStage,
    status: ReviewRunStepStatus,
    offset_seconds: int,
    duration: float | None,
    *,
    tokens: int | None = None,
    error: str | None = None,
) -> ReviewRunStep:
    return ReviewRunStep(
        id=_id(5, n),
        review_run_id=run.id,
        stage=stage,
        status=status,
        started_at=run.created_at + timedelta(seconds=offset_seconds),
        duration_seconds=duration,
        tokens_used=tokens,
        error_code=error,
    )


def _finding(
    n: int,
    run: ReviewRun,
    path: str,
    line: int,
    category: FindingCategory,
    severity: FindingSeverity,
    message: str,
    suggestion: str | None,
) -> Finding:
    return Finding(
        id=_id(6, n),
        review_run_id=run.id,
        anchor=DiffAnchor(file_path=path, side=DiffSide.NEW, new_line=line),
        category=category,
        severity=severity,
        message=message,
        created_at=run.updated_at,
        updated_at=run.updated_at,
        suggestion=suggestion,
        confidence=0.9,
    )
