"""Кэш списка репозиториев, видимых пользователю, поверх `InstallationCatalog`.

Видимость прогонов и репозиториев (`GET /reviews`, `GET /events`, ручки прогона и
репозиториев) спрашивает GitHub на каждый запрос: обход всех установок
пользователя постранично. Кабинет перезапрашивает список прогонов на события
потока, и без кэша это десятки тысяч запросов в час при лимите токена 5000 (#126).

Кэшируется только ответ `list_repositories` — что пользователь видит на GitHub.
Подключён ли репозиторий, вызывающий по-прежнему читает из базы, поэтому
подключение само кэш не трогает. Но ответ GitHub меняется, когда пользователь
ставит приложение на новый репозиторий: страница подключения
(`GET /repositories/available`) всегда спрашивает GitHub и пишет свежий ответ в
кэш (`read=False`). Подключить можно только оттуда, поэтому к моменту
подключения кэш уже знает о новом репозитории. Цена кэша — отзыв доступа на
GitHub виден с задержкой до TTL.

Ключ — sha256 токена: тот же пользователь с тем же токеном. Обновлённый токен
или выход — другой ключ, старая запись недостижима и уходит по TTL; сам токен в
кэше не хранится. Сбои GitHub не кэшируются. Сбой хранилища — промах, а не
ошибка (`CacheStore`). Проверки подключения (`user_repository`, `app_installed`)
идут мимо кэша.

Хранилище — `CacheStore` процесса API: процесс один, кэш в нём корректен. Со
второй репликой у каждой будет свой кэш — запросов к GitHub больше, но ответы
верные; общий кэш — замена адаптера `CacheStore` на Redis (change
`visible-repositories-cache`).
"""

import hashlib
import json
import logging
from dataclasses import asdict
from typing import Any

from app.application.ports.cache_store import CacheStore
from app.application.ports.installation_catalog import InstallationCatalog
from app.domain.entities import InstallationRepository, RepositoryPermissions, Secret

logger = logging.getLogger(__name__)

_CACHE_PREFIX = "visible-repositories:v1"


def visible_repositories_key(access_token: Secret) -> str:
    return f"{_CACHE_PREFIX}:{hashlib.sha256(access_token.reveal().encode()).hexdigest()}"


class CachedInstallationCatalog:
    def __init__(
        self, catalog: InstallationCatalog, cache: CacheStore, ttl_seconds: int, *, read: bool = True
    ) -> None:
        self._catalog = catalog
        self._cache = cache
        self._ttl_seconds = ttl_seconds
        # `False` — всегда к GitHub, ответ — в кэш: страница подключения.
        self._read_cache = read

    def list_repositories(self, access_token: Secret) -> list[InstallationRepository]:
        key = visible_repositories_key(access_token)
        cached = self._read(key) if self._read_cache else None
        if cached is not None:
            return cached
        repositories = self._catalog.list_repositories(access_token)
        self._write(key, repositories)
        return repositories

    def user_repository(self, access_token: Secret, provider_id: str) -> InstallationRepository | None:
        return self._catalog.user_repository(access_token, provider_id)

    def app_installed(self, full_name: str) -> bool:
        return self._catalog.app_installed(full_name)

    def _read(self, key: str) -> list[InstallationRepository] | None:
        try:
            raw = self._cache.get(key)
            if raw is None:
                return None
            return [_from_json(item) for item in json.loads(raw)]
        except Exception:
            # Ключ — хэш токена: в лог он не идёт, хватит префикса.
            logger.warning("кэш видимых репозиториев: не удалось прочитать запись", exc_info=True)
            return None

    def _write(self, key: str, repositories: list[InstallationRepository]) -> None:
        try:
            self._cache.set(key, json.dumps([_to_json(r) for r in repositories]), self._ttl_seconds)
        except Exception:
            logger.warning("кэш видимых репозиториев: не удалось записать запись", exc_info=True)


def _to_json(repository: InstallationRepository) -> dict[str, Any]:
    return asdict(repository)


def _from_json(item: dict[str, Any]) -> InstallationRepository:
    # Запись пишет только `_write`; битая — исключение, `_read` считает её промахом.
    return InstallationRepository(**{**item, "permissions": RepositoryPermissions(**item["permissions"])})
