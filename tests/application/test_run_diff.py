"""Use case `load_run_diff`: дифф прогона для кабинета — кэш, установка, шлюз.

Без сети: каталог установок, VCS-шлюз — фейки, кэш — настоящий in-memory.
"""

from uuid import uuid4

import pytest

from app.application.use_cases.run_diff import (
    RepositoryNotInstalled,
    RunCommitNotFound,
    RunDiffUnavailable,
    load_run_diff,
)
from app.domain.diff_view import DiffFileView, DiffHunkView, DiffLine
from app.domain.entities import Repository, ReviewRun
from app.domain.enums import (
    DiffFileStatus,
    DiffLineKind,
    Language,
    ReviewRunStatus,
    TriggerSource,
)
from app.domain.vcs_errors import (
    DiffTooLargeError,
    VcsError,
    VcsNotFoundError,
    VcsTransientError,
)
from app.infrastructure.cache.memory import InMemoryCacheStore
from tests.fakes.installation_catalog import FakeInstallationCatalog
from tests.fakes.webhooks import NOW, FakeVcs, a_repository

# Переименование с правкой, бинарный и не-ASCII строки: разбор записи из кэша должен
# вернуть каждое поле представления как было.
RICH_DIFF = (
    "diff --git a/app/old.py b/app/new.py\n"
    "similarity index 90%\n"
    "rename from app/old.py\n"
    "rename to app/new.py\n"
    "--- a/app/old.py\n+++ b/app/new.py\n"
    "@@ -1,2 +1,2 @@ def main():\n"
    " x = 1\n"
    "-print('привет')\n"
    "+print('пока')\n"
    "diff --git a/logo.png b/logo.png\n"
    "new file mode 100644\n"
    "Binary files /dev/null and b/logo.png differ\n"
)
BASE_SHA = "b" * 40
HEAD_SHA = "a" * 40
MAX_BYTES = 10_000


def a_run(*, base_sha: str | None = BASE_SHA) -> ReviewRun:
    return ReviewRun(
        id=uuid4(),
        merge_request_id=uuid4(),
        head_sha=HEAD_SHA,
        status=ReviewRunStatus.COMPLETED,
        trigger=TriggerSource.WEBHOOK,
        last_progress_at=NOW,
        created_at=NOW,
        updated_at=NOW,
        base_sha=base_sha,
    )


def load(
    run: ReviewRun,
    repository: Repository,
    *,
    installations: FakeInstallationCatalog,
    vcs: FakeVcs,
    cache: InMemoryCacheStore,
    max_bytes: int = MAX_BYTES,
) -> tuple[DiffFileView, ...]:
    return load_run_diff(
        run,
        repository,
        installations=installations,
        vcs=vcs,
        cache=cache,
        max_bytes=max_bytes,
    )


def test_run_without_base_sha_is_unavailable_without_any_lookups() -> None:
    """Пары коммитов нет — не к чему обращаться: ни каталог, ни шлюз, ни кэш."""
    installations = FakeInstallationCatalog()
    vcs = FakeVcs()
    cache = InMemoryCacheStore()
    reads: list[str] = []
    cache.get = lambda key: reads.append(key)  # type: ignore[method-assign]

    with pytest.raises(RunDiffUnavailable):
        load(a_run(base_sha=None), a_repository(), installations=installations, vcs=vcs, cache=cache)

    assert installations.calls == []
    assert vcs.calls == []
    assert reads == []


# `DIFF` из `tests.fakes.webhooks`: правка первой строки `app/main.py`.
MAIN_PY_VIEW = (
    DiffFileView(
        path="app/main.py",
        previous_path=None,
        status=DiffFileStatus.MODIFIED,
        is_binary=False,
        language=Language.PYTHON,
        additions=1,
        deletions=1,
        hunks=(
            DiffHunkView(
                header="",
                old_start=1,
                old_count=1,
                new_start=1,
                new_count=1,
                lines=(
                    DiffLine(DiffLineKind.REMOVED, 1, None, "old"),
                    DiffLine(DiffLineKind.ADDED, None, 1, "new"),
                ),
            ),
        ),
    ),
)


def test_miss_asks_the_installation_fetches_the_pair_and_caches_it_by_commits() -> None:
    """Промах: номер установки, дифф пары коммитов прогона, запись по ключу репозитория и пары."""
    repository = a_repository()
    installations = FakeInstallationCatalog(installation_ids={repository.full_name: 77})
    vcs = FakeVcs()
    cache = InMemoryCacheStore()

    view = load(a_run(), repository, installations=installations, vcs=vcs, cache=cache)

    assert view == MAIN_PY_VIEW
    assert installations.calls == ["repository_installation_id"]
    assert vcs.calls == [("diff", repository.full_name, BASE_SHA, HEAD_SHA, 77)]
    assert cache.get(f"run-diff:v1:{repository.id}:{BASE_SHA}:{HEAD_SHA}") is not None


def test_hit_touches_neither_the_catalog_nor_the_gateway_and_gives_an_equal_view() -> None:
    """Попадание: ни каталога, ни шлюза; представление равно полученному на промахе."""
    repository = a_repository()
    run = a_run()
    cache = InMemoryCacheStore()
    fetched = load(
        run,
        repository,
        installations=FakeInstallationCatalog(),
        vcs=FakeVcs(diff=RICH_DIFF),
        cache=cache,
    )
    installations = FakeInstallationCatalog()
    vcs = FakeVcs(diff=RICH_DIFF)

    cached = load(run, repository, installations=installations, vcs=vcs, cache=cache)

    assert cached == fetched
    assert [f.status for f in cached] == [DiffFileStatus.RENAMED, DiffFileStatus.ADDED]
    assert installations.calls == []
    assert vcs.calls == []


def test_cache_keeps_the_raw_diff_for_an_hour() -> None:
    """В кэше — сырой текст диффа, TTL — час: запись по паре коммитов не устаревает."""
    repository = a_repository()
    now = [0.0]
    cache = InMemoryCacheStore(monotonic=lambda: now[0])
    load(a_run(), repository, installations=FakeInstallationCatalog(), vcs=FakeVcs(diff=RICH_DIFF), cache=cache)

    assert cache.get(_key(repository)) == RICH_DIFF
    now[0] = 3599.0
    assert cache.get(_key(repository)) == RICH_DIFF
    now[0] = 3600.0
    assert cache.get(_key(repository)) is None


def _key(repository: Repository) -> str:
    return f"run-diff:v1:{repository.id}:{BASE_SHA}:{HEAD_SHA}"


def test_app_not_installed_is_refused_without_fetching_the_diff() -> None:
    repository = a_repository()
    vcs = FakeVcs()
    cache = InMemoryCacheStore()

    with pytest.raises(RepositoryNotInstalled):
        load(
            a_run(),
            repository,
            installations=FakeInstallationCatalog(not_installed={repository.full_name}),
            vcs=vcs,
            cache=cache,
        )

    assert vcs.calls == []
    assert cache.get(_key(repository)) is None


def test_diff_over_the_callers_limit_is_too_large() -> None:
    """Лимит — аргумент вызывающего (`RUN_DIFF_MAX_BYTES`): дифф на байт больше — отказ."""
    repository = a_repository()
    vcs = FakeVcs()
    cache = InMemoryCacheStore()

    with pytest.raises(DiffTooLargeError):
        load(
            a_run(),
            repository,
            installations=FakeInstallationCatalog(),
            vcs=vcs,
            cache=cache,
            max_bytes=len(vcs.diff.encode()) - 1,
        )

    assert cache.get(_key(repository)) is None


def test_provider_not_knowing_the_commit_means_the_commit_is_gone() -> None:
    repository = a_repository()
    cache = InMemoryCacheStore()

    with pytest.raises(RunCommitNotFound):
        load(
            a_run(),
            repository,
            installations=FakeInstallationCatalog(),
            vcs=FakeVcs(error=VcsNotFoundError("404")),
            cache=cache,
        )

    assert cache.get(_key(repository)) is None


@pytest.mark.parametrize("error", [VcsError("403"), VcsTransientError("503")])
def test_other_provider_failures_pass_through_and_are_not_cached(error: VcsError) -> None:
    repository = a_repository()
    cache = InMemoryCacheStore()
    run = a_run()

    with pytest.raises(type(error)) as caught:
        load(run, repository, installations=FakeInstallationCatalog(), vcs=FakeVcs(error=error), cache=cache)

    assert caught.value is error
    vcs = FakeVcs()
    load(run, repository, installations=FakeInstallationCatalog(), vcs=vcs, cache=cache)
    assert len(vcs.calls) == 1


def test_installation_lookup_failure_passes_through() -> None:
    error = VcsTransientError("timeout")
    vcs = FakeVcs()

    with pytest.raises(VcsTransientError):
        load(
            a_run(),
            a_repository(),
            installations=FakeInstallationCatalog(error=error),
            vcs=vcs,
            cache=InMemoryCacheStore(),
        )

    assert vcs.calls == []


class _BrokenStore:
    """Хранилище, у которого отказывают и чтение, и запись."""

    def get(self, key: str) -> str | None:
        raise OSError("хранилище недоступно")

    def set(self, key: str, value: str, ttl_seconds: int) -> None:
        raise OSError("хранилище недоступно")


def test_store_failure_is_a_miss_and_does_not_break_the_answer() -> None:
    """Сбой хранилища — промах: дифф берётся у провайдера, ответ не ломается."""
    vcs = FakeVcs()

    view = load_run_diff(
        a_run(),
        a_repository(),
        installations=FakeInstallationCatalog(),
        vcs=vcs,
        cache=_BrokenStore(),
        max_bytes=MAX_BYTES,
    )

    assert view == MAIN_PY_VIEW
    assert len(vcs.calls) == 1
