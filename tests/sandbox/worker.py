"""Ядро песочницы: настоящий consume-цикл и `run_review` на фейках.

Боевой код: `Worker.run`, формат сообщения (`to_wire_message`), обработчик
`build_review_handler`, промпты из `prompts/review/`, `GitHubAppAuth` и
`GitHubVcsGateway`, AST-экстрактор, адаптер модели из `build_llm_gateway`.
Подменены: брокер (`FakeChannel`, `FakeConnection`), база
(`FakeUnitOfWork`), кэш (`InMemoryCacheStore`) и GitHub (`FakeGitHub` на
уровне HTTP). Postgres, RabbitMQ и Docker не нужны.
"""

import hashlib
import json
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any
from uuid import UUID

from pydantic import SecretStr

from app.application.ports.llm_gateway import LlmGateway
from app.config import Settings
from app.domain.entities import (
    MergeRequest,
    Repository,
    ReviewJob,
    ReviewRun,
)
from app.domain.enums import (
    MergeRequestState,
    Provider,
    ReviewRunStatus,
    TriggerSource,
)
from app.domain.repo_rules import RULES_PATH
from app.infrastructure.cache.memory import InMemoryCacheStore
from app.infrastructure.container import WORKER_VCS_LIMITS, build_llm_gateway
from app.infrastructure.parsing.tree_sitter_extractor import (
    TreeSitterDefinitionExtractor,
)
from app.infrastructure.prompts import DEFAULT_PROMPTS_DIR
from app.infrastructure.queue.rabbitmq import to_wire_message
from app.infrastructure.vcs.github import GitHubVcsGateway
from app.infrastructure.vcs.github_auth import GitHubAppAuth
from app.worker.factory import (
    Worker,
    build_review_handler,
    context_limits,
    llm_limits,
    load_checked_prompt_texts,
)

from ..eval.rules.corpus import CONFIGS
from ..eval.scoring import ExpectedIssue
from ..fakes.github_api import (
    APP_ID,
    INSTALLATION_ID,
    REPO_FULL_NAME,
    REPO_PROVIDER_ID,
    FakeGitHub,
    app_private_key,
)
from ..fakes.queue import FakeChannel, FakeConnection
from ..fakes.unit_of_work import FakeUnitOfWork
from .inputs import PrInput

REPOSITORY_ID = UUID(int=0x700100)
# Заглушки адресов: песочница к ним не ходит, но `Settings` их требует.
PLACEHOLDER_DATABASE_URL = "postgresql+psycopg://sandbox:sandbox@localhost:1/sandbox"
PLACEHOLDER_RABBITMQ_URL = "amqp://sandbox:sandbox@localhost:1/"
PLACEHOLDER_WEBHOOK_SECRET = "sandbox-webhook-secret"


@dataclass(frozen=True, slots=True)
class SandboxConfig:
    """Что выбрал пользователь CLI: модель, правила и лимиты окна.

    `llm` - режим модели CLI: `ollama` - настоящая Ollama, любой другой
    (`stub`, сценарий, оракул) для настроек воркера означает заглушку.
    """

    llm: str = "ollama"
    ollama_url: str = "http://localhost:11434"
    model: str = ""
    rules_config: str = "rules"
    window_tokens: int | None = None
    context_max_tokens: int | None = None
    max_output_tokens: int | None = None


# --- зависимости обработчика ----------------------------------------------


class _Deps:
    """То, что обработчик берёт у `Container`: общие база, кэш и шлюзы на весь прогон."""

    def __init__(
        self,
        uow: FakeUnitOfWork,
        llm: LlmGateway,
        vcs: GitHubVcsGateway,
    ) -> None:
        self._uow = uow
        self._llm = llm
        self._vcs = vcs
        self._extractor = TreeSitterDefinitionExtractor()
        self._cache = InMemoryCacheStore()

    def unit_of_work(self) -> FakeUnitOfWork:
        return self._uow

    def llm_gateway(self) -> LlmGateway:
        return self._llm

    def vcs_gateway(self) -> GitHubVcsGateway:
        return self._vcs

    def definition_extractor(self) -> TreeSitterDefinitionExtractor:
        return self._extractor

    def cache_store(self) -> InMemoryCacheStore:
        return self._cache


# --- настройки и наполнение -----------------------------------------------


def build_settings(
    config: SandboxConfig, private_key: str, *, external_llm: bool = False
) -> Settings:
    """Настройки воркера без `.env`: всё, что важно песочнице, задано явно.

    Ollama - только для `llm="ollama"` без своего шлюза модели (`external_llm`);
    иначе адаптер в настройках - заглушка, чтобы они оставались валидными.
    """
    ollama = config.llm == "ollama" and not external_llm
    overrides: dict[str, Any] = {}
    if config.window_tokens is not None:
        overrides["llm_context_window_tokens"] = config.window_tokens
    if config.context_max_tokens is not None:
        overrides["context_max_tokens"] = config.context_max_tokens
    if config.max_output_tokens is not None:
        overrides["llm_max_output_tokens"] = config.max_output_tokens
    return Settings(
        _env_file=None,
        database_url=PLACEHOLDER_DATABASE_URL,
        rabbitmq_url=PLACEHOLDER_RABBITMQ_URL,
        github_webhook_secret=SecretStr(PLACEHOLDER_WEBHOOK_SECRET),
        github_app_id=APP_ID,
        github_app_private_key=SecretStr(private_key),
        llm_backend="ollama" if ollama else "stub",
        ollama_base_url=config.ollama_url,
        ollama_model=config.model if ollama else "",
        llm_hosted_fallback=False,
        **overrides,
    )


def _sha(*parts: object) -> str:
    return hashlib.sha1("/".join(map(str, parts)).encode()).hexdigest()


def _seed_repository(uow: FakeUnitOfWork, now: datetime) -> None:
    uow.repositories.add(
        Repository(
            id=REPOSITORY_ID,
            provider=Provider.GITHUB,
            provider_id=REPO_PROVIDER_ID,
            full_name=REPO_FULL_NAME,
            default_branch="develop",
            auto_review_enabled=True,
            created_at=now,
            updated_at=now,
            connected_at=now,
        )
    )


def seed_case(
    pr: PrInput, n: int, github: FakeGitHub, uow: FakeUnitOfWork, rules_config: str
) -> ReviewJob:
    """PR в «GitHub», его MR и прогон `queued` в базе; возвращает сообщение очереди."""
    rules_text = CONFIGS[rules_config]
    base_files = dict(pr.base_files)
    if rules_text is not None:
        base_files[RULES_PATH] = rules_text
    base_sha = _sha("base", rules_config, pr.id, n)
    head_sha = _sha("head", rules_config, pr.id, n)
    pull = github.open_pull(
        n,
        base_sha=base_sha,
        head_sha=head_sha,
        base_files=base_files,
        head_files=dict(pr.head_files),
        title=pr.pr_title,
        body=pr.pr_description,
        head_ref=f"sandbox/{pr.id}",
    )
    now = datetime.now(UTC)
    merge_request_id = UUID(int=0x1000 + n)
    run_id = UUID(int=0x2000 + n)
    uow.merge_requests.add(
        MergeRequest(
            id=merge_request_id,
            repository_id=REPOSITORY_ID,
            number=n,
            title=pull.title,
            description=pull.body,
            author=pull.author,
            source_branch=pull.head_ref,
            target_branch=pull.base_ref,
            head_sha=head_sha,
            state=MergeRequestState.OPEN,
            created_at=now,
            updated_at=now,
        )
    )
    uow.review_runs.add(
        ReviewRun(
            id=run_id,
            merge_request_id=merge_request_id,
            head_sha=head_sha,
            status=ReviewRunStatus.QUEUED,
            trigger=TriggerSource.WEBHOOK,
            last_progress_at=now,
            created_at=now,
            updated_at=now,
            base_sha=base_sha,
        )
    )
    return ReviewJob(
        id=UUID(int=0x3000 + n),
        review_run_id=run_id,
        event_type="pull_request",
        action="opened",
        installation_id=INSTALLATION_ID,
        repository_provider_id=REPO_PROVIDER_ID,
        repository_full_name=REPO_FULL_NAME,
        pull_request_number=n,
        head_sha=head_sha,
        base_sha=base_sha,
    )


# --- результат --------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class StepResult:
    name: str
    status: str
    duration_seconds: float | None
    tokens_used: int | None
    error_code: str | None


@dataclass(frozen=True, slots=True)
class FindingResult:
    path: str
    line: int | None
    category: str
    severity: str
    message: str
    rule: str | None


@dataclass(frozen=True, slots=True)
class InlineResult:
    path: str
    line: int
    body: str
    # Категория найденного замечания на той же строке; `None` - не сопоставилось.
    category: str | None


@dataclass(frozen=True, slots=True)
class PublishedReview:
    body: str
    event: str
    comments: tuple[InlineResult, ...]


@dataclass(frozen=True, slots=True)
class CaseResult:
    case_id: str
    title: str
    number: int
    status: str
    failure_reason: str | None
    model: str | None
    duration_seconds: float | None
    steps: tuple[StepResult, ...]
    findings: tuple[FindingResult, ...]
    reviews: tuple[PublishedReview, ...]
    # Чем кончилась доставка сообщения PR: ack или nack (ушло бы в DLQ).
    acked: bool
    nacked: bool
    expected: tuple[ExpectedIssue, ...]
    unexpected_requests: tuple[tuple[str, str], ...] = field(default_factory=tuple)


@dataclass(frozen=True, slots=True)
class SandboxResult:
    cases: list[CaseResult]


@dataclass(frozen=True, slots=True)
class _Delivery:
    """Атрибуты `method`, которые читает `Worker.run`: песочница доставляет каждое сообщение впервые."""

    delivery_tag: int
    redelivered: bool = False


def _collect(
    case: PrInput,
    n: int,
    job: ReviewJob,
    uow: FakeUnitOfWork,
    github: FakeGitHub,
    channel: FakeChannel,
    unexpected: tuple[tuple[str, str], ...],
) -> CaseResult:
    run = uow.review_runs.get(job.review_run_id)
    assert run is not None, f"прогона {job.review_run_id} нет в базе"
    steps = tuple(
        StepResult(
            name=step.stage.value,
            status=step.status.value,
            duration_seconds=step.duration_seconds,
            tokens_used=step.tokens_used,
            error_code=step.error_code,
        )
        for step in sorted(
            uow.review_run_steps.list_for_run(run.id), key=lambda s: s.started_at
        )
    )
    findings = tuple(
        FindingResult(
            path=f.anchor.file_path,
            line=f.anchor.line,
            category=f.category.value,
            severity=f.severity.value,
            message=f.message,
            rule=f"{f.rule.name}#{f.rule.check}" if f.rule else None,
        )
        for f in uow.findings.list_for_run(run.id)
    )
    categories = {(f.path, f.line): f.category for f in findings}
    reviews = tuple(
        PublishedReview(
            body=review.body,
            event=review.event,
            comments=tuple(
                InlineResult(c.path, c.line, c.body, categories.get((c.path, c.line)))
                for c in review.comments
            ),
        )
        for review in github.reviews_of(n)
    )
    return CaseResult(
        case_id=case.id,
        title=case.title,
        number=n,
        status=run.status.value,
        failure_reason=run.failure_reason,
        model=run.model,
        duration_seconds=run.duration_seconds,
        steps=steps,
        findings=findings,
        reviews=reviews,
        # Тег доставки PR - его номер: одно сообщение на PR, теги с единицы.
        acked=n in channel.acked,
        nacked=n in channel.nacked,
        expected=case.expected,
        unexpected_requests=unexpected,
    )


def run_sandbox(
    prs: Sequence[PrInput],
    config: SandboxConfig,
    *,
    llm: LlmGateway | None = None,
) -> SandboxResult:
    """Прогнать PR через настоящий воркер; одно сообщение очереди на PR.

    `llm` - свой шлюз модели вместо адаптера из настроек. `WorkerConfigError`
    из проверки окна выходит наружу до первого сообщения.
    """
    private_key = app_private_key()
    settings = build_settings(config, private_key, external_llm=llm is not None)
    # До наполнения: с непригодным окном песочница не стартует, как и воркер.
    prompt_texts = load_checked_prompt_texts(settings, DEFAULT_PROMPTS_DIR)

    github = FakeGitHub(REPO_FULL_NAME, INSTALLATION_ID, private_key)
    client = github.client()
    client.timeout = WORKER_VCS_LIMITS.timeout
    auth = GitHubAppAuth(APP_ID, private_key, client)
    vcs = GitHubVcsGateway(
        auth,
        client,
        sleep=lambda _: None,
        rate_limit_budget_seconds=WORKER_VCS_LIMITS.rate_limit_budget_seconds,
    )

    uow = FakeUnitOfWork()
    _seed_repository(uow, datetime.now(UTC))
    numbers = range(1, len(prs) + 1)
    jobs = [
        seed_case(pr, n, github, uow, config.rules_config)
        for n, pr in zip(numbers, prs, strict=True)
    ]
    # Без заголовков: `consuming_span` начнёт новый трейс, как для сообщения без
    # traceparent. Тег доставки - номер PR.
    deliveries = [
        (
            _Delivery(delivery_tag=n),
            SimpleNamespace(headers={}, type=None),
            json.dumps(to_wire_message(job)).encode("utf-8"),
        )
        for n, job in zip(numbers, jobs, strict=True)
    ]

    handler = build_review_handler(
        _Deps(uow, llm if llm is not None else build_llm_gateway(settings), vcs),
        context_limits(settings),
        llm_limits(settings),
        prompt_texts,
    )
    channel = FakeChannel(deliveries)

    # Неожиданные запросы к GitHub - по PR: обработчик оборачивается и снимает
    # срез `github.unexpected` до и после своего сообщения. Чьё сообщение
    # обрабатывается, говорит канал (`current`), а не порядок вызовов.
    unexpected_by_case: dict[int, list[tuple[str, str]]] = {}

    def handle(body: bytes, kind: str | None) -> None:
        assert channel.current is not None, "обработчик вызван вне доставки"
        n = numbers[channel.current]
        before = len(github.unexpected)
        try:
            handler(body)
        finally:
            unexpected_by_case.setdefault(n, []).extend(github.unexpected[before:])

    worker = Worker(connection=FakeConnection(), channel=channel)  # type: ignore[arg-type]
    worker.run(handle, limit=len(deliveries))

    return SandboxResult(
        cases=[
            _collect(
                pr, n, job, uow, github, channel, tuple(unexpected_by_case.get(n, ()))
            )
            for n, pr, job in zip(numbers, prs, jobs, strict=True)
        ]
    )
