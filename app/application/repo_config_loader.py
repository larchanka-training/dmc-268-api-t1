"""RepoConfigLoader: правила ревью владельца из `.review/rules.md`.

Файл довыбирается на `base_sha` — коммите целевой ветки PR, а не на
`head_sha`: правила из самого PR пишет его автор, и читать их значило бы
дать ему переписать критерии собственного ревью или протащить инструкцию
модели (design D1). Ключ кэша «репозиторий, base_sha» неизменяем, поэтому
запись не инвалидируется, а только вытесняется.

Загрузка best-effort: нет файла, сбой VCS, файл больше лимита — дефолтный
фоллбэк (ревью по стандартным критериям), прогон не падает. Сбой VCS не
кэшируется — следующий прогон повторит запрос. Сбой самого кэша — промах.

В записи кэша — время получения от VCS (design D8 change `repository-settings`):
попадание отдаёт его, а не время чтения. Запись без времени (формат `v1`) —
промах, поэтому префикс ключа — `v2`.
"""

import json
import logging
from datetime import datetime

from app.application.ports.cache_store import CacheStore
from app.application.ports.vcs_gateway import VcsGateway
from app.domain.entities import RepoRules
from app.domain.enums import RulesStatus
from app.domain.repo_rules import RULES_PATH, parse_rules_markdown
from app.domain.vcs_errors import VcsError, VcsFileTooLargeError

logger = logging.getLogger(__name__)

_CACHE_PREFIX = "repo-rules:v2"


def rules_cache_key(repo_full_name: str, base_sha: str) -> str:
    return f"{_CACHE_PREFIX}:{repo_full_name}:{base_sha}"


def load_repo_rules(
    repo_full_name: str,
    base_sha: str,
    installation_id: int,
    *,
    vcs: VcsGateway,
    cache: CacheStore,
    max_bytes: int,
    cache_ttl_seconds: int,
    now: datetime,
) -> RepoRules:
    key = rules_cache_key(repo_full_name, base_sha)
    cached = _read_cache(cache, key)
    if cached is not None:
        status, text, fetched_at = cached
    else:
        fetched_at = now
        try:
            content = vcs.fetch_file(
                repo_full_name, RULES_PATH, base_sha, installation_id, max_bytes=max_bytes
            )
            status, text = _classify(content)
        except VcsFileTooLargeError:
            # Тело не дочитано; по sha оно не изменится — исход кэшируется.
            status, text = RulesStatus.TOO_LARGE, ""
        except VcsError:
            logger.warning(
                "правила %s@%s недоступны — ревью по стандартным критериям",
                repo_full_name,
                base_sha,
                exc_info=True,
            )
            return RepoRules(status=RulesStatus.UNAVAILABLE)
        _write_cache(cache, key, status, text, fetched_at, cache_ttl_seconds)

    if status is not RulesStatus.LOADED:
        logger.info("правила %s@%s: %s — дефолтный фоллбэк", repo_full_name, base_sha, status)
        return RepoRules(status=status, fetched_at=fetched_at)
    rules, rejected = parse_rules_markdown(text)
    if not rules:
        # Итог разбора, а не довыборки: в кэше лежит текст, статус выводится из него.
        logger.info(
            "правила %s@%s: ни одного принятого правила из %d разделов — дефолтный фоллбэк",
            repo_full_name,
            base_sha,
            len(rejected),
        )
        return RepoRules(status=RulesStatus.EMPTY, rejected=rejected, fetched_at=fetched_at)
    if rejected:
        logger.info(
            "правила %s@%s: отклонено %d из %d",
            repo_full_name,
            base_sha,
            len(rejected),
            len(rules) + len(rejected),
        )
    return RepoRules(
        status=RulesStatus.LOADED, rules=rules, rejected=rejected, fetched_at=fetched_at
    )


def _classify(content: str | None) -> tuple[RulesStatus, str]:
    if content is None:
        return RulesStatus.ABSENT, ""
    return RulesStatus.LOADED, content


def _read_cache(cache: CacheStore, key: str) -> tuple[RulesStatus, str, datetime] | None:
    """Запись без `fetched_at` (прежний формат) и битая запись — промах."""
    try:
        raw = cache.get(key)
        if raw is None:
            return None
        data = json.loads(raw)
        fetched_at = datetime.fromisoformat(data["fetched_at"])
        return RulesStatus(data["status"]), str(data["text"]), fetched_at
    except Exception as error:  # noqa: BLE001 — битая запись и сбой хранилища — промах, а не отказ
        # В лог ключ и тип ошибки, без exc_info: текст ошибки разбора повторяет значение из записи.
        logger.warning("кэш правил: не удалось прочитать %s: %s", key, type(error).__name__)
        return None


def _write_cache(
    cache: CacheStore, key: str, status: RulesStatus, text: str, fetched_at: datetime, ttl: int
) -> None:
    try:
        value = {"status": status.value, "text": text, "fetched_at": fetched_at.isoformat()}
        cache.set(key, json.dumps(value), ttl)
    except Exception as error:  # noqa: BLE001 — сбой хранилища не ломает загрузку, файл уже получен
        logger.warning("кэш правил: не удалось записать %s: %s", key, type(error).__name__)
