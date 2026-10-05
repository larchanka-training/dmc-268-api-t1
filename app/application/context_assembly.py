"""Заготовка сборки контекста.

Временный шов на месте будущего `VcsGateway`/tree-sitter: не ходит никуда,
только пакует то, что уже есть в `ReviewJob`, в `ContextPayload` с одним
уровнем `diff`. Чистая функция — время и id приходят аргументами. Не порт и
не адаптер: нет внешней системы, которую он бы транспортировал.
"""

import hashlib
import json
from collections.abc import Callable
from datetime import datetime
from uuid import UUID

from app.domain.entities import ContextPayload, Hunk, ReviewJob

# Синтетический файл/диапазон строк для единственного hunk'а заглушки.
# Не соответствует ничему настоящему в репозитории — существует, чтобы
# `validate_anchor`/`add_validated` имели на что сработать до появления
# реального VcsGateway.
PLACEHOLDER_FILE_PATH = "PLACEHOLDER.md"


def stub_hunks() -> tuple[Hunk, ...]:
    return (
        Hunk(
            file_path=PLACEHOLDER_FILE_PATH,
            new_start=1,
            new_count=1,
            changed_new_lines=frozenset({1}),
        ),
    )


def assemble_context_stub(
    job: ReviewJob,
    *,
    review_run_id: UUID,
    now: datetime,
    new_id: Callable[[], UUID],
) -> ContextPayload:
    body = {
        "note": "заготовка: реальная сборка контекста (VcsGateway/AST) не реализована",
        "job_id": str(job.id),
        "review_run_id": str(job.review_run_id),
        "head_sha": job.head_sha,
    }
    digest = hashlib.sha256(json.dumps(body, sort_keys=True).encode("utf-8")).hexdigest()
    return ContextPayload(
        id=new_id(),
        review_run_id=review_run_id,
        chunk_index=0,
        tiers=("diff",),
        file_paths=(PLACEHOLDER_FILE_PATH,),
        token_count=0,
        content_sha256=digest,
        body=body,
        created_at=now,
        updated_at=now,
    )
