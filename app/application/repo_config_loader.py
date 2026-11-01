"""RepoConfigLoader: правила ревью владельца из `.review/rules.md`.

Файл довыбирается на `base_sha` — коммите целевой ветки PR, а не на
`head_sha`: правила из самого PR пишет его автор, и читать их значило бы
дать ему переписать критерии собственного ревью или протащить инструкцию
модели (design D1). Ключ кэша «репозиторий, base_sha» неизменяем, поэтому
запись не инвалидируется, а только вытесняется.

Загрузка best-effort: нет файла, сбой VCS, файл больше лимита — дефолтный
фоллбэк (ревью по стандартным критериям), прогон не падает. Сбой VCS не
кэшируется — следующий прогон повторит запрос. Сбой самого кэша — промах.
"""

import json
import logging

from app.application.ports.cache_store import CacheStore
from app.application.ports.vcs_gateway import VcsGateway
from app.domain.entities import RepoRules
from app.domain.enums import RulesStatus
from app.domain.repo_rules import RULES_PATH, rules_from_text
from app.domain.vcs_errors import VcsError, VcsFileTooLargeError

logger = logging.getLogger(__name__)

_CACHE_PREFIX = "repo-rules:v1"


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
) -> RepoRules:
    key = rules_cache_key(repo_full_name, base_sha)
    cached = _read_cache(cache, key)
    if cached is not None:
        status, text = cached
    else:
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
        _write_cache(cache, key, status, text, cache_ttl_seconds)

    if status is not RulesStatus.LOADED:
        logger.info("правила %s@%s: %s — дефолтный фоллбэк", repo_full_name, base_sha, status)
        return RepoRules(status=status)
    parsed = rules_from_text(text)
    if parsed.status is RulesStatus.EMPTY:
        # Итог разбора, а не довыборки: в кэше лежит текст, статус выводится из него.
        logger.info(
            "правила %s@%s: ни одного принятого правила из %d разделов — дефолтный фоллбэк",
            repo_full_name,
            base_sha,
            len(parsed.rejected),
        )
    elif parsed.rejected:
        logger.info(
            "правила %s@%s: отклонено %d из %d",
            repo_full_name,
            base_sha,
            len(parsed.rejected),
            len(parsed.rules) + len(parsed.rejected),
        )
    return parsed


def _classify(content: str | None) -> tuple[RulesStatus, str]:
    if content is None:
        return RulesStatus.ABSENT, ""
    return RulesStatus.LOADED, content


def _read_cache(cache: CacheStore, key: str) -> tuple[RulesStatus, str] | None:
    try:
        raw = cache.get(key)
        if raw is None:
            return None
        data = json.loads(raw)
        return RulesStatus(data["status"]), str(data["text"])
    except Exception:
        logger.warning("кэш правил: не удалось прочитать %s", key, exc_info=True)
        return None


def _write_cache(cache: CacheStore, key: str, status: RulesStatus, text: str, ttl: int) -> None:
    try:
        cache.set(key, json.dumps({"status": status.value, "text": text}), ttl)
    except Exception:
        logger.warning("кэш правил: не удалось записать %s", key, exc_info=True)
