"""Загрузка `.review/rules.md`: целевая ветка, кэш, дефолтный фоллбэк — на фейках."""

import json

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

RULES_MD = """\
## Errors

- pattern: app/**/*.py
- exclude: **/tests/**

1. Отказ — значение Result

## Broken

- pattern: app/**
"""


def _load(vcs: FakeVcs, cache: FakeCache, *, max_bytes: int = 10_000) -> RepoRules:
    return load_repo_rules(
        REPO, BASE, INSTALLATION, vcs=vcs, cache=cache, max_bytes=max_bytes, cache_ttl_seconds=60
    )


def test_rules_are_read_from_the_target_branch_commit() -> None:
    vcs = FakeVcs(files={(PATH, BASE): RULES_MD, (PATH, HEAD): "## Подмена из PR\n"})
    rules = _load(vcs, FakeCache())
    assert vcs.calls == [("file", REPO, PATH, BASE, INSTALLATION)]
    assert rules.status is RulesStatus.LOADED
    assert [r.name for r in rules.rules] == ["Errors"]
    assert rules.rejected == (
        RejectedRule("Broken", "нет исключения (exclude); нет проверок"),
    )


def test_missing_file_falls_back_and_is_cached() -> None:
    vcs, cache = FakeVcs(), FakeCache()
    assert _load(vcs, cache) == RepoRules(status=RulesStatus.ABSENT)
    assert _load(vcs, cache) == RepoRules(status=RulesStatus.ABSENT)
    assert len([c for c in vcs.calls if c[0] == "file"]) == 1


def test_too_large_file_is_not_parsed_and_text_is_not_cached() -> None:
    vcs, cache = FakeVcs(files={(PATH, BASE): RULES_MD}), FakeCache()
    assert _load(vcs, cache, max_bytes=10) == RepoRules(status=RulesStatus.TOO_LARGE)
    (_, value, _), = cache.sets
    assert json.loads(value) == {"status": "too_large", "text": ""}


def test_vcs_failure_falls_back_and_is_not_cached() -> None:
    vcs = FakeVcs(file_errors={PATH: VcsError("503")})
    cache = FakeCache()
    assert _load(vcs, cache) == RepoRules(status=RulesStatus.UNAVAILABLE)
    assert cache.sets == []
    # Следующий прогон снова идёт в VCS — и получает файл, когда тот доступен.
    vcs.file_errors.clear()
    vcs.files[(PATH, BASE)] = RULES_MD
    assert _load(vcs, cache).status is RulesStatus.LOADED


def test_cache_hit_does_not_touch_vcs() -> None:
    cache = FakeCache(
        data={rules_cache_key(REPO, BASE): json.dumps({"status": "loaded", "text": RULES_MD})}
    )
    vcs = FakeVcs()
    rules = _load(vcs, cache)
    assert vcs.calls == []
    assert [r.name for r in rules.rules] == ["Errors"]


def test_loaded_rules_are_cached_with_ttl() -> None:
    cache = FakeCache()
    _load(FakeVcs(files={(PATH, BASE): RULES_MD}), cache)
    ((key, value, ttl),) = cache.sets
    assert key == rules_cache_key(REPO, BASE)
    assert json.loads(value) == {"status": "loaded", "text": RULES_MD}
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
