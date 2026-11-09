"""Перевод между строками таблиц и доменными сущностями.

Здесь нет ветвлений по бизнес-правилам. Если кажется, что правило живёт в
маппере, его место в чистой функции, с которой сверяется вызывающий код.
"""

from copy import deepcopy
from decimal import Decimal

from app.domain.entities import (
    DiffAnchor,
    Finding,
    MergeRequest,
    PublishedComment,
    Repository,
    ReviewRun,
    ReviewRunStep,
    RuleRef,
    Session,
    StoredContextPayload,
    User,
)
from app.domain.llm_usage import LlmUsage
from app.infrastructure.db.models import (
    ContextPayloadRow,
    FindingRow,
    MergeRequestRow,
    PublishedCommentRow,
    RepositoryRow,
    ReviewRunRow,
    ReviewRunStepRow,
    SessionRow,
    UserRow,
)


def repository_to_domain(row: RepositoryRow) -> Repository:
    return Repository(
        id=row.id,
        provider=row.provider,
        provider_id=row.provider_id,
        full_name=row.full_name,
        default_branch=row.default_branch,
        auto_review_enabled=row.auto_review_enabled,
        created_at=row.created_at,
        updated_at=row.updated_at,
        connected_at=row.connected_at,
        branch_filter=tuple(row.branch_filter),
        min_severity=row.min_severity,
        payer_user_id=row.payer_user_id,
    )


def merge_request_to_domain(row: MergeRequestRow) -> MergeRequest:
    return MergeRequest(
        id=row.id,
        repository_id=row.repository_id,
        number=row.number,
        title=row.title,
        description=row.description,
        author=row.author,
        source_branch=row.source_branch,
        target_branch=row.target_branch,
        head_sha=row.head_sha,
        state=row.state,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def review_run_to_domain(row: ReviewRunRow) -> ReviewRun:
    return ReviewRun(
        id=row.id,
        merge_request_id=row.merge_request_id,
        head_sha=row.head_sha,
        base_sha=row.base_sha,
        status=row.status,
        trigger=row.trigger,
        last_progress_at=row.last_progress_at,
        created_at=row.created_at,
        updated_at=row.updated_at,
        failure_reason=row.failure_reason,
        failed_stage=row.failed_stage,
        model=row.model,
        tokens_used=row.tokens_used,
        usage=_usage(row),
        duration_seconds=row.duration_seconds,
        rejected_findings=row.rejected_findings,
        verification=row.verification,
        verifier_dropped_findings=row.verifier_dropped_findings,
        payer_user_id=row.payer_user_id,
        estimated_cost_eur=row.estimated_cost_eur,
        coverage_reason=row.coverage_reason,
    )


def _usage(row: ReviewRunRow) -> LlmUsage | None:
    # Колонки пишутся вместе (`usage_columns`): пустая одна - пусты все.
    if row.input_tokens is None:
        return None
    return LlmUsage(
        input_tokens=row.input_tokens,
        output_tokens=row.output_tokens or 0,
        cached_tokens=row.cached_tokens or 0,
        reasoning_tokens=row.reasoning_tokens or 0,
        total_tokens=row.tokens_used or 0,
        cost_eur=row.cost_eur if row.cost_eur is not None else Decimal(0),
    )


def usage_columns(usage: LlmUsage | None) -> dict[str, int | Decimal | None]:
    """Колонки `review_runs` расхода модели: все пусты, если расхода нет."""
    if usage is None:
        return dict.fromkeys(_USAGE_COLUMNS)
    return {column: getattr(usage, column) for column in _USAGE_COLUMNS}


def run_usage_columns(run: ReviewRun) -> dict[str, int | Decimal | None]:
    """Колонки расхода прогона вместе с `tokens_used`.

    Итог берётся из `usage`, когда он есть: `tokens_used` и `usage.total_tokens`
    не расходятся в базе, даже если `replace(run, usage=...)` забыл `tokens_used`.
    """
    columns = usage_columns(run.usage)
    columns["tokens_used"] = run.usage.total_tokens if run.usage is not None else run.tokens_used
    return columns


# Итог (`total_tokens`) хранится в `tokens_used`.
_USAGE_COLUMNS = ("input_tokens", "output_tokens", "cached_tokens", "reasoning_tokens", "cost_eur")


def review_run_step_to_domain(row: ReviewRunStepRow) -> ReviewRunStep:
    return ReviewRunStep(
        id=row.id,
        review_run_id=row.review_run_id,
        stage=row.stage,
        status=row.status,
        started_at=row.started_at,
        duration_seconds=row.duration_seconds,
        tokens_used=row.tokens_used,
        error_code=row.error_code,
    )


def context_payload_to_domain(row: ContextPayloadRow) -> StoredContextPayload:
    return StoredContextPayload(
        id=row.id,
        review_run_id=row.review_run_id,
        chunk_index=row.chunk_index,
        tiers=tuple(row.tiers),
        file_paths=tuple(row.file_paths),
        token_count=row.token_count,
        content_sha256=row.content_sha256,
        # Копия, как tiers и file_paths выше. Отдать dict строки по ссылке —
        # значит превратить frozen dataclass в живую ручку к состоянию сессии,
        # где правка payload.body уходит во flush в базу.
        body=deepcopy(row.body) if row.body is not None else None,
        body_expired_at=row.body_expired_at,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def finding_to_domain(row: FindingRow) -> Finding:
    return Finding(
        id=row.id,
        review_run_id=row.review_run_id,
        anchor=DiffAnchor(
            file_path=row.file_path,
            side=row.side,
            old_line=row.old_line,
            new_line=row.new_line,
        ),
        category=row.category,
        severity=row.severity,
        message=row.message,
        created_at=row.created_at,
        updated_at=row.updated_at,
        suggestion=row.suggestion,
        confidence=row.confidence,
        rule=_rule_ref(row),
        covered_by_finding_id=row.covered_by_finding_id,
    )


def _rule_ref(row: FindingRow) -> RuleRef | None:
    # Ограничение ck_findings_rule_ref держит три колонки вместе.
    if row.rule_name is None or row.rule_check is None or row.rule_check_text is None:
        return None
    return RuleRef(name=row.rule_name, check=row.rule_check, check_text=row.rule_check_text)


def published_comment_to_domain(row: PublishedCommentRow) -> PublishedComment:
    return PublishedComment(
        id=row.id,
        review_run_id=row.review_run_id,
        provider_comment_id=row.provider_comment_id,
        kind=row.kind,
        published_at=row.published_at,
        created_at=row.created_at,
        updated_at=row.updated_at,
        finding_id=row.finding_id,
        thread_resolved=row.thread_resolved,
        thread_resolved_by_bot=row.thread_resolved_by_bot,
        in_reply_to_provider_comment_id=row.in_reply_to_provider_comment_id,
        feedback_positive=row.feedback_positive,
        feedback_negative=row.feedback_negative,
    )


def user_to_domain(row: UserRow) -> User:
    return User(
        id=row.id,
        provider=row.provider,
        provider_user_id=row.provider_user_id,
        login=row.login,
        name=row.name,
        avatar_url=row.avatar_url,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def session_to_domain(row: SessionRow) -> Session:
    return Session(
        id=row.id,
        token_hash=row.token_hash,
        user_id=row.user_id,
        created_at=row.created_at,
        expires_at=row.expires_at,
    )
