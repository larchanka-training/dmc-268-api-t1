"""Загрузка `.review/rules.md`: целевая ветка, кэш, дефолтный фоллбэк — на фейках."""

import json
from datetime import UTC, datetime

import pytest

from app.application.repo_config_loader import load_repo_rules, rules_cache_key
from app.domain.entities import RejectedRule, RepoRules
from app.domain.enums import RulesStatus
from app.domain.vcs_errors import VcsError

from ..fakes.context import FakeCache
from ..fakes.webhooks import FakeVcs

REPO = "owner/repo"
BASE = "b" * 40
HEAD = "a" * 40
INSTALLATION = 7
PATH = ".review/rules.md"
NOW = datetime(2026, 10, 11, 10, 0, tzinfo=UTC)
LATER = datetime(2026, 10, 11, 11, 30, tzinfo=UTC)

RULES_MD = """\
## Errors

- pattern: app/**/*.py
- exclude: **/tests/**

1. Отказ — значение Result

## Broken

- pattern: app/**
"""


def _load(
    vcs: FakeVcs, cache: FakeCache, *, max_bytes: int = 10_000, now: datetime = NOW
) -> RepoRules:
    return load_repo_rules(
        REPO,
        BASE,
        INSTALLATION,
        vcs=vcs,
        cache=cache,
        max_bytes=max_bytes,
        cache_ttl_seconds=60,
        now=now,
    )


def _entry(status: str, text: str, fetched_at: datetime = NOW) -> str:
    return json.dumps({"status": status, "text": text, "fetched_at": fetched_at.isoformat()})


def test_rules_are_read_from_the_target_branch_commit() -> None:
    vcs = FakeVcs(files={(PATH, BASE): RULES_MD, (PATH, HEAD): "## Подмена из PR\n"})
    rules = _load(vcs, FakeCache())
    assert vcs.calls == [("file", REPO, PATH, BASE, INSTALLATION)]
    assert rules.status is RulesStatus.LOADED
    assert [r.name for r in rules.rules] == ["Errors"]
    assert rules.rejected == (
        RejectedRule("Broken", "нет проверок"),
    )


def test_missing_file_falls_back_and_is_cached() -> None:
    vcs, cache = FakeVcs(), FakeCache()
    assert _load(vcs, cache) == RepoRules(status=RulesStatus.ABSENT, fetched_at=NOW)
    assert _load(vcs, cache, now=LATER) == RepoRules(status=RulesStatus.ABSENT, fetched_at=NOW)
    assert len([c for c in vcs.calls if c[0] == "file"]) == 1


def test_too_large_file_is_not_parsed_and_text_is_not_cached() -> None:
    vcs, cache = FakeVcs(files={(PATH, BASE): RULES_MD}), FakeCache()
    assert _load(vcs, cache, max_bytes=10) == RepoRules(
        status=RulesStatus.TOO_LARGE, fetched_at=NOW
    )
    assert vcs.file_limits == [10]
    (_, value, _), = cache.sets
    assert json.loads(value) == {
        "status": "too_large",
        "text": "",
        "fetched_at": "2026-10-11T10:00:00+00:00",
    }


@pytest.mark.parametrize(
    "text",
    ["", "   \n\n", "# Правила проекта\n\nПишите аккуратно и проверяйте всё.\n"],
    ids=["empty", "whitespace", "prose-only"],
)
def test_file_without_rule_sections_is_empty(text: str) -> None:
    assert _load(FakeVcs(files={(PATH, BASE): text}), FakeCache()) == RepoRules(
        status=RulesStatus.EMPTY, fetched_at=NOW
    )


def test_file_where_every_section_is_rejected_is_empty_and_keeps_the_reasons() -> None:
    text = "## Broken\n\n- pattern: app/**\n\n## Nameless scope\n\n1. Проверка без паттерна\n"
    assert _load(FakeVcs(files={(PATH, BASE): text}), FakeCache()) == RepoRules(
        status=RulesStatus.EMPTY,
        rejected=(
            RejectedRule("Broken", "нет проверок"),
            RejectedRule("Nameless scope", "нет паттерна (pattern)"),
        ),
        fetched_at=NOW,
    )


def test_empty_file_is_cached_as_loaded_text() -> None:
    """`empty` - итог разбора, а не довыборки: кэш хранит текст, как у `loaded`."""
    vcs, cache = FakeVcs(files={(PATH, BASE): ""}), FakeCache()
    _load(vcs, cache)
    ((_, value, _),) = cache.sets
    assert json.loads(value) == {
        "status": "loaded",
        "text": "",
        "fetched_at": "2026-10-11T10:00:00+00:00",
    }
    assert _load(vcs, cache).status is RulesStatus.EMPTY
    assert len([c for c in vcs.calls if c[0] == "file"]) == 1


def test_vcs_failure_falls_back_and_is_not_cached() -> None:
    vcs = FakeVcs(file_errors={PATH: VcsError("503")})
    cache = FakeCache()
    assert _load(vcs, cache) == RepoRules(status=RulesStatus.UNAVAILABLE)
    assert _load(vcs, cache).fetched_at is None
    assert cache.sets == []
    # Следующий прогон снова идёт в VCS — и получает файл, когда тот доступен.
    vcs.file_errors.clear()
    vcs.files[(PATH, BASE)] = RULES_MD
    assert _load(vcs, cache).status is RulesStatus.LOADED


def test_cache_hit_does_not_touch_vcs() -> None:
    cache = FakeCache(data={rules_cache_key(REPO, BASE): _entry("loaded", RULES_MD)})
    vcs = FakeVcs()
    rules = _load(vcs, cache, now=LATER)
    assert vcs.calls == []
    assert [r.name for r in rules.rules] == ["Errors"]
    # Время первого получения от VCS, а не время чтения из кэша.
    assert rules.fetched_at == NOW


def test_loaded_rules_are_cached_with_ttl() -> None:
    cache = FakeCache()
    _load(FakeVcs(files={(PATH, BASE): RULES_MD}), cache)
    ((key, value, ttl),) = cache.sets
    assert key == rules_cache_key(REPO, BASE)
    assert json.loads(value) == {
        "status": "loaded",
        "text": RULES_MD,
        "fetched_at": "2026-10-11T10:00:00+00:00",
    }
    assert ttl == 60


def test_broken_cache_is_a_miss_not_a_failure() -> None:
    rules = _load(FakeVcs(files={(PATH, BASE): RULES_MD}), FakeCache(broken=True))
    assert rules.status is RulesStatus.LOADED


def test_garbage_in_cache_is_a_miss() -> None:
    cache = FakeCache(data={rules_cache_key(REPO, BASE): "не json"})
    vcs = FakeVcs(files={(PATH, BASE): RULES_MD})
    assert _load(vcs, cache).status is RulesStatus.LOADED
    assert vcs.calls  # пошли в VCS


def test_cache_key_separates_commits() -> None:
    assert rules_cache_key(REPO, BASE) != rules_cache_key(REPO, HEAD)


def test_cache_key_is_versioned_v2() -> None:
    """Запись `v1` без времени под новым ключом не читается: префикс сменён."""
    assert rules_cache_key(REPO, BASE) == f"repo-rules:v2:{REPO}:{BASE}"


def test_entry_without_fetched_at_is_a_miss_and_is_refetched() -> None:
    old = json.dumps({"status": "loaded", "text": RULES_MD})
    cache = FakeCache(data={rules_cache_key(REPO, BASE): old})
    vcs = FakeVcs(files={(PATH, BASE): RULES_MD})
    rules = _load(vcs, cache, now=LATER)
    assert vcs.calls == [("file", REPO, PATH, BASE, INSTALLATION)]
    assert rules.fetched_at == LATER
    ((_, value, _),) = cache.sets
    assert json.loads(value)["fetched_at"] == "2026-10-11T11:30:00+00:00"


def test_fetched_at_survives_cache_for_every_cached_status() -> None:
    vcs, cache = FakeVcs(files={(PATH, BASE): RULES_MD}), FakeCache()
    first = _load(vcs, cache)
    second = _load(vcs, cache, now=LATER)
    assert first == second
    assert second.fetched_at == NOW
