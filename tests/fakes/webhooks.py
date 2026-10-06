"""Двойники приёма вебхуков: VCS и очередь, builder'ы и записанные payload'ы.

Тестам приёма нужна база без сети: событие собирается из fixture'ов, VCS и
очередь хранят сущности как есть и записывают вызовы, поэтому тесты сверяют
исход с точными записями и задачей.
"""

import datetime as dt
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from uuid import uuid4

from app.domain.entities import PRMetadata, Repository, ReviewJob
from app.domain.enums import MergeRequestState, Provider
from app.domain.vcs_errors import VcsError

FIXTURES = Path(__file__).parent.parent / "fixtures"
NOW = dt.datetime(2026, 9, 29, 12, 0, tzinfo=dt.UTC)

REGISTERED_PROVIDER_ID = "923478362"
INSTALLATION_ID = 512804923
REPO_FULL_NAME = "larchanka-training/dmc-268-api-t1"
DIFF = (
    "diff --git a/app/main.py b/app/main.py\n"
    "--- a/app/main.py\n+++ b/app/main.py\n"
    "@@ -1 +1 @@\n-old\n+new\n"
)


def webhook_payload(action: str = "opened") -> dict[str, Any]:
    """Записанный payload GitHub; для прочих действий — тело `opened` с заменой."""
    name = (
        "github_webhook_synchronize.json"
        if action == "synchronize"
        else "github_webhook_opened.json"
    )
    raw: dict[str, Any] = json.loads((FIXTURES / name).read_text(encoding="utf-8"))
    raw["action"] = action
    return raw


def pr_metadata(base_sha: str = "fedcba0987654321fedcba0987654321fedcba09") -> PRMetadata:
    """Свежие метаданные VCS.

    Заголовок, автор и ветки нарочно отличаются от payload'а вебхука: так
    тест видит, что в `MergeRequest` ложится версия из метаданных, а не
    устаревшая копия из доставленного тела.
    """
    return PRMetadata(
        number=6,
        head_sha="a1b2c3d4e5f6789012345678abcdef0123456789",
        base_sha=base_sha,
        title="feat: метаданные из VCS свежее payload'а",
        author="grinv",
        source_branch="feat/webhook-intake-rebased",
        target_branch="main",
        state=MergeRequestState.OPEN,
    )


def a_repository() -> Repository:
    return Repository(
        id=uuid4(),
        provider=Provider.GITHUB,
        provider_id=REGISTERED_PROVIDER_ID,
        full_name=REPO_FULL_NAME,
        default_branch="develop",
        auto_review_enabled=True,
        created_at=NOW,
        updated_at=NOW,
    )


@dataclass
class FakeVcs:
    diff: str = DIFF
    meta: PRMetadata = field(default_factory=pr_metadata)
    error: VcsError | None = None
    calls: list[tuple[Any, ...]] = field(default_factory=list)

    def fetch_diff(
        self, repo_full_name: str, base_sha: str, head_sha: str, installation_id: int
    ) -> str:
        self.calls.append(("diff", repo_full_name, base_sha, head_sha, installation_id))
        if self.error is not None:
            raise self.error
        return self.diff

    def fetch_pr_metadata(
        self, repo_full_name: str, pr_number: int, installation_id: int
    ) -> PRMetadata:
        self.calls.append(("metadata", repo_full_name, pr_number, installation_id))
        if self.error is not None:
            raise self.error
        return self.meta


@dataclass
class FakeQueue:
    jobs: list[ReviewJob] = field(default_factory=list)

    def enqueue(self, job: ReviewJob) -> None:
        self.jobs.append(job)
