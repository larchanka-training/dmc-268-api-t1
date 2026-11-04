"""Use case `load_default_branch_rules`: статус правил ветки по умолчанию для кабинета.

Без сети: каталог установок и VCS-шлюз — фейки, кэш — настоящий in-memory.
"""

import datetime as dt
from dataclasses import replace

import pytest

from app.application.repo_config_loader import load_repo_rules
from app.application.use_cases.repository_rules import load_default_branch_rules
from app.application.use_cases.run_diff import RepositoryNotInstalled
from app.domain.entities import RejectedRule, RepoRules, Repository
from app.domain.enums import RulesStatus
from app.domain.vcs_errors import VcsNotFoundError, VcsTransientError
from app.infrastructure.cache.memory import InMemoryCacheStore
from tests.fakes.installation_catalog import FakeInstallationCatalog
from tests.fakes.webhooks import NOW, REPO_FULL_NAME, FakeVcs, a_repository

PATH = ".review/rules.md"
INSTALLATION = 42
SHA = "c" * 40
NEW_SHA = "d" * 40
LATER = NOW + dt.timedelta(hours=1)
MAX_BYTES = 10_000

RULES_MD = """\
## Errors

- pattern: app/**/*.py

1. Отказ — значение Result

## Broken

- pattern: app/**
"""


def installations(*, installation_id_error: Exception | None = None) -> FakeInstallationCatalog:
    return FakeInstallationCatalog(
        installation_ids={REPO_FULL_NAME: INSTALLATION}, installation_id_error=installation_id_error
    )


def load(
    repository: Repository,
    *,
    catalog: FakeInstallationCatalog,
    vcs: FakeVcs,
    cache: InMemoryCacheStore,
    now: dt.datetime = NOW,
    max_bytes: int = MAX_BYTES,
) -> RepoRules:
    return load_default_branch_rules(
        repository,
        installations=catalog,
        vcs=vcs,
        cache=cache,
        max_bytes=max_bytes,
        ttl_seconds=3600,
        now=now,
    )


def file_calls(vcs: FakeVcs) -> list[tuple[object, ...]]:
    return [c for c in vcs.calls if c[0] == "file"]


def test_rules_are_read_at_the_default_branch_commit() -> None:
    vcs = FakeVcs(branch_shas={"develop": SHA}, files={(PATH, SHA): RULES_MD})
    rules = load(a_repository(), catalog=installations(), vcs=vcs, cache=InMemoryCacheStore())

    assert vcs.calls == [
        ("branch_sha", REPO_FULL_NAME, "develop", INSTALLATION),
        ("file", REPO_FULL_NAME, PATH, SHA, INSTALLATION),
    ]
    assert rules.status is RulesStatus.LOADED
    assert [r.name for r in rules.rules] == ["Errors"]
    assert rules.rejected == (RejectedRule("Broken", "нет проверок"),)
    assert rules.fetched_at == NOW


@pytest.mark.parametrize(
    ("files", "max_bytes", "expected"),
    [
        ({(PATH, SHA): "## Broken\n\n- pattern: app/**\n"}, MAX_BYTES, RulesStatus.EMPTY),
        ({}, MAX_BYTES, RulesStatus.ABSENT),
        ({(PATH, SHA): RULES_MD}, 10, RulesStatus.TOO_LARGE),
    ],
    ids=["empty", "absent", "too_large"],
)
def test_other_file_outcomes_keep_the_fetch_time(
    files: dict[tuple[str, str], str], max_bytes: int, expected: RulesStatus
) -> None:
    vcs = FakeVcs(branch_shas={"develop": SHA}, files=files)
    rules = load(
        a_repository(), catalog=installations(), vcs=vcs, cache=InMemoryCacheStore(), max_bytes=max_bytes
    )
    assert rules.status is expected
    assert rules.rules == ()
    assert rules.fetched_at == NOW


def test_file_failure_is_unavailable_and_not_cached() -> None:
    vcs = FakeVcs(branch_shas={"develop": SHA}, file_errors={PATH: VcsTransientError("503")})
    cache = InMemoryCacheStore()
    assert load(a_repository(), catalog=installations(), vcs=vcs, cache=cache) == RepoRules(
        status=RulesStatus.UNAVAILABLE
    )
    vcs.file_errors.clear()
    vcs.files[(PATH, SHA)] = RULES_MD
    assert load(a_repository(), catalog=installations(), vcs=vcs, cache=cache).status is RulesStatus.LOADED


def test_not_installed_raises_without_branch_or_file_requests() -> None:
    catalog = FakeInstallationCatalog(not_installed={REPO_FULL_NAME})
    vcs = FakeVcs(branch_shas={"develop": SHA}, files={(PATH, SHA): RULES_MD})

    with pytest.raises(RepositoryNotInstalled):
        load(a_repository(), catalog=catalog, vcs=vcs, cache=InMemoryCacheStore())

    assert vcs.calls == []


def test_installation_lookup_failure_is_unavailable_without_branch_or_file_requests() -> None:
    catalog = installations(installation_id_error=VcsTransientError("502"))
    vcs = FakeVcs(branch_shas={"develop": SHA}, files={(PATH, SHA): RULES_MD})

    rules = load(a_repository(), catalog=catalog, vcs=vcs, cache=InMemoryCacheStore())

    assert rules == RepoRules(status=RulesStatus.UNAVAILABLE)
    assert vcs.calls == []


@pytest.mark.parametrize(
    "vcs",
    [
        FakeVcs(files={(PATH, SHA): RULES_MD}),  # ветки нет: GitHub отвечает 422 — `VcsError`
        FakeVcs(error=VcsNotFoundError("404"), files={(PATH, SHA): RULES_MD}),
        FakeVcs(error=VcsTransientError("503"), files={(PATH, SHA): RULES_MD}),
    ],
    ids=["missing-branch", "repository-not-found", "provider-down"],
)
def test_branch_resolution_failure_is_unavailable_without_file_request(vcs: FakeVcs) -> None:
    cache = InMemoryCacheStore()
    rules = load(a_repository(), catalog=installations(), vcs=vcs, cache=cache)

    assert rules == RepoRules(status=RulesStatus.UNAVAILABLE)
    assert file_calls(vcs) == []


def test_same_commit_is_fetched_once_and_keeps_the_first_time() -> None:
    vcs = FakeVcs(branch_shas={"develop": SHA}, files={(PATH, SHA): RULES_MD})
    cache = InMemoryCacheStore()
    first = load(a_repository(), catalog=installations(), vcs=vcs, cache=cache, now=NOW)
    second = load(a_repository(), catalog=installations(), vcs=vcs, cache=cache, now=LATER)

    assert second == first
    assert second.fetched_at == NOW
    assert len(file_calls(vcs)) == 1
    # Ветка разрешается на каждый запрос: иначе новый коммит не увидеть.
    assert len([c for c in vcs.calls if c[0] == "branch_sha"]) == 2


def test_new_commit_is_fetched_again_with_new_time() -> None:
    vcs = FakeVcs(branch_shas={"develop": SHA}, files={(PATH, SHA): RULES_MD, (PATH, NEW_SHA): RULES_MD})
    cache = InMemoryCacheStore()
    load(a_repository(), catalog=installations(), vcs=vcs, cache=cache, now=NOW)
    vcs.branch_shas["develop"] = NEW_SHA

    rules = load(a_repository(), catalog=installations(), vcs=vcs, cache=cache, now=LATER)

    assert file_calls(vcs)[-1] == ("file", REPO_FULL_NAME, PATH, NEW_SHA, INSTALLATION)
    assert len(file_calls(vcs)) == 2
    assert rules.fetched_at == LATER


def test_saved_default_branch_is_used() -> None:
    vcs = FakeVcs(branch_shas={"main": SHA}, files={(PATH, SHA): RULES_MD})
    repository = replace(a_repository(), default_branch="main")
    assert load(repository, catalog=installations(), vcs=vcs, cache=InMemoryCacheStore()).status is (
        RulesStatus.LOADED
    )


def test_uses_the_run_cache_key_for_the_same_commit() -> None:
    """Ключ тот же, что у прогона на `base_sha`: запись прогона читается без запроса файла."""
    cache = InMemoryCacheStore()
    load_repo_rules(
        REPO_FULL_NAME,
        SHA,
        INSTALLATION,
        vcs=FakeVcs(files={(PATH, SHA): RULES_MD}),
        cache=cache,
        max_bytes=MAX_BYTES,
        cache_ttl_seconds=3600,
        now=NOW,
    )
    vcs = FakeVcs(branch_shas={"develop": SHA})

    rules = load(a_repository(), catalog=installations(), vcs=vcs, cache=cache, now=LATER)

    assert rules.status is RulesStatus.LOADED
    assert rules.fetched_at == NOW
    assert file_calls(vcs) == []
