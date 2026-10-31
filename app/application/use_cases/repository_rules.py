"""Статус правил репозитория для кабинета (change `repository-settings`, D7).

Номер установки приложения → sha ветки по умолчанию → `load_repo_rules` на этом
sha: тот же лимит, разбор, статусы и ключ кэша, что у прогона, поэтому запись
прогона на `base_sha` того же коммита в том же кэше читается без запроса файла.
Кэш процессный: записи воркера процессу API не видны. Ветка разрешается на
каждый вызов — иначе новый коммит не увидеть; кэшируется только исход файла.

Приложение не установлено — `RepositoryNotInstalled`: это состояние, которое
пользователь исправляет установкой, а не сбой. Сбой GitHub на номере установки
или на разрешении ветки (ветки нет — `422`, репозиторий недоступен — `404`) —
статус `unavailable`, как сбой чтения файла, и не кэшируется.
"""

import logging
from datetime import datetime

from app.application.ports.cache_store import CacheStore
from app.application.ports.installation_catalog import InstallationCatalog
from app.application.ports.vcs_gateway import VcsGateway
from app.application.repo_config_loader import load_repo_rules
from app.application.use_cases.run_diff import RepositoryNotInstalled
from app.domain.entities import RepoRules, Repository
from app.domain.enums import RulesStatus
from app.domain.vcs_errors import VcsError

logger = logging.getLogger(__name__)


def load_default_branch_rules(
    repository: Repository,
    *,
    installations: InstallationCatalog,
    vcs: VcsGateway,
    cache: CacheStore,
    max_bytes: int,
    ttl_seconds: int,
    now: datetime,
) -> RepoRules:
    """Исход загрузки `.review/rules.md` с последнего коммита ветки по умолчанию.

    `RepositoryNotInstalled` — приложение не установлено или установка
    приостановлена; ветка при этом не разрешается, файл не запрашивается.
    """
    try:
        installation_id = installations.repository_installation_id(repository.full_name)
    except VcsError as error:
        # Только тип ошибки: текст ответа провайдера в лог не идёт.
        logger.warning(
            "правила %s: номер установки недоступен: %s", repository.full_name, type(error).__name__
        )
        return RepoRules(status=RulesStatus.UNAVAILABLE)
    if installation_id is None:
        raise RepositoryNotInstalled(f"приложение не установлено на {repository.full_name}")
    try:
        sha = vcs.resolve_branch_sha(repository.full_name, repository.default_branch, installation_id)
    except VcsError as error:
        logger.warning(
            "правила %s: ветка по умолчанию не разрешена: %s", repository.full_name, type(error).__name__
        )
        return RepoRules(status=RulesStatus.UNAVAILABLE)
    return load_repo_rules(
        repository.full_name,
        sha,
        installation_id,
        vcs=vcs,
        cache=cache,
        max_bytes=max_bytes,
        cache_ttl_seconds=ttl_seconds,
        now=now,
    )
