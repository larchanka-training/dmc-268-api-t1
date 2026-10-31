"""Кэш списка видимых пользователю репозиториев и номера установки поверх `InstallationCatalog`.

Видимость прогонов и репозиториев (`GET /reviews`, `GET /events`, ручки прогона и
репозиториев) спрашивает GitHub на каждый запрос: обход всех установок
пользователя постранично. Кабинет перезапрашивает список прогонов на события
потока, и без кэша это десятки тысяч запросов в час при лимите токена 5000 (#126).

Кэшируются два ответа: `list_repositories` — что пользователь видит на GitHub,
и `repository_installation_id` — номер установки приложения на репозитории.
Подключён ли репозиторий, вызывающий по-прежнему читает из базы, поэтому
подключение само кэш не трогает. Но ответ GitHub меняется, когда пользователь
ставит приложение на новый репозиторий: возврат с экрана установки (Setup URL,
change `repositories-one-list`) спрашивает GitHub и пишет свежий ответ в кэш
(`read=False`). Список репозиториев (`GET /repositories`) читает кэш. Цена
кэша — отзыв доступа на GitHub и установка мимо Setup URL видны с задержкой до TTL.

Номер установки нужен диффу прогона в кабинете (change `review-run-diff`): без
кэша каждое открытие прогона — лишний запрос к GitHub с JWT приложения. Ключ —
`installation-id:v1:{full_name}`, без токена: номер от пользователя не зависит,
а видимость репозитория вызывающий проверил раньше. Значение — число строкой,
TTL свой (`INSTALLATION_ID_CACHE_TTL_SECONDS`). `None` (не установлено или
приостановлено) не кэшируется: только что поставленное приложение видно сразу.
Цена — удалённая или приостановленная установка видна с задержкой до TTL.
`read=False` действует на оба кэшированных ответа: и список, и номер установки
берутся у GitHub, свежий ответ пишется в кэш.

Ключ списка — sha256 токена: тот же пользователь с тем же токеном. Обновлённый токен
или выход — другой ключ, старая запись недостижима и уходит по TTL; сам токен в
кэше не хранится. Сбои GitHub не кэшируются. Сбой хранилища — промах, а не
ошибка (`CacheStore`). Проверки подключения (`user_repository`, `app_installed`)
и установка Setup URL (`installation_selection`, `installation_repositories`) идут
мимо кэша.

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
from app.domain.enums import RepositorySelection

logger = logging.getLogger(__name__)

_CACHE_PREFIX = "visible-repositories:v1"
_INSTALLATION_ID_PREFIX = "installation-id:v1"


def visible_repositories_key(access_token: Secret) -> str:
    return f"{_CACHE_PREFIX}:{hashlib.sha256(access_token.reveal().encode()).hexdigest()}"


def installation_id_key(full_name: str) -> str:
    return f"{_INSTALLATION_ID_PREFIX}:{full_name}"


class CachedInstallationCatalog:
    def __init__(
        self,
        catalog: InstallationCatalog,
        cache: CacheStore,
        ttl_seconds: int,
        *,
        installation_id_ttl_seconds: int,
        read: bool = True,
    ) -> None:
        self._catalog = catalog
        self._cache = cache
        self._ttl_seconds = ttl_seconds
        self._installation_id_ttl_seconds = installation_id_ttl_seconds
        # `False` — всегда к GitHub, ответ — в кэш: возврат с установки приложения.
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

    def installation_selection(self, access_token: Secret, installation_id: int) -> RepositorySelection | None:
        return self._catalog.installation_selection(access_token, installation_id)

    def installation_repositories(self, access_token: Secret, installation_id: int) -> list[InstallationRepository]:
        return self._catalog.installation_repositories(access_token, installation_id)

    def app_installed(self, full_name: str) -> bool:
        return self._catalog.app_installed(full_name)

    def repository_installation_id(self, full_name: str) -> int | None:
        key = installation_id_key(full_name)
        cached = self._read_installation_id(key) if self._read_cache else None
        if cached is not None:
            return cached
        installation_id = self._catalog.repository_installation_id(full_name)
        if installation_id is not None:
            try:
                self._cache.set(key, str(installation_id), self._installation_id_ttl_seconds)
            except Exception:
                logger.warning("кэш номера установки: не удалось записать запись", exc_info=True)
        return installation_id

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

    def _read_installation_id(self, key: str) -> int | None:
        try:
            raw = self._cache.get(key)
            # Запись пишет только `repository_installation_id`; не число — битая, промах.
            return int(raw) if raw is not None else None
        except Exception:
            logger.warning("кэш номера установки: не удалось прочитать запись", exc_info=True)
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
