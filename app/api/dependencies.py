"""Проводка FastAPI.

Роутеры просят зависимость, типизированную портом, и никогда не называют
конкретный адаптер.
"""

import time
from collections.abc import Callable, Iterator
from typing import Annotated, cast

from fastapi import Depends, Path, Request

from app.application.ports import RunChanges, UnitOfWork
from app.application.ports.cache_store import CacheStore
from app.application.ports.installation_catalog import InstallationCatalog
from app.application.ports.job_queue import JobQueue
from app.application.ports.oauth_provider import OAuthProvider
from app.application.ports.vcs_gateway import VcsGateway
from app.config import Settings
from app.domain.enums import Provider
from app.infrastructure.container import Container


def get_container(request: Request) -> Container:
    # `Request.app.state` — это `State` из Starlette, чей `__getattr__`
    # типизирован как возвращающий `Any`; composition root — единственный, кто
    # пишет этот атрибут (см. `create_app`), поэтому приведение закрывает
    # границу `Any` здесь, а не пускает её в каждого, кто берёт эту зависимость.
    return cast(Container, request.app.state.container)


def get_unit_of_work(request: Request) -> Iterator[UnitOfWork]:
    with get_container(request).unit_of_work() as uow:
        yield uow


def get_job_queue(container: Container = Depends(get_container)) -> JobQueue:
    return container.job_queue()


def get_installation_catalog(container: Container = Depends(get_container)) -> InstallationCatalog:
    return container.installation_catalog()


def get_visibility_catalog(
    catalog: InstallationCatalog = Depends(get_installation_catalog),
    container: Container = Depends(get_container),
) -> InstallationCatalog:
    """Каталог с кэшем видимых репозиториев: видимость прогонов и репозиториев (#126).

    Подключение берёт `get_installation_catalog` — без кэша.
    """
    return container.visibility_catalog(catalog)


def get_refreshing_catalog(
    catalog: InstallationCatalog = Depends(get_installation_catalog),
    container: Container = Depends(get_container),
) -> InstallationCatalog:
    """Каталог, который всегда спрашивает GitHub и пишет свежий ответ в кэш видимости:
    возврат с установки приложения (Setup URL, design D3)."""
    return container.visibility_catalog(catalog, read=False)


def get_app_slug(container: Container = Depends(get_container)) -> Callable[[], str | None]:
    """Как узнать slug GitHub App; вызывается в теле ручки — при `422` запроса к GitHub нет."""
    return container.app_slug


def get_token_provider(container: Container = Depends(get_container)) -> OAuthProvider:
    """Провайдер, выдавший токен сессии, — обновляет и отзывает его: сессии кабинета — только GitHub."""
    provider = container.oauth_provider(Provider.GITHUB.value)
    if provider is None:
        raise RuntimeError("провайдер входа github не собран в контейнере")
    return provider


def get_unit_of_work_factory(
    container: Container = Depends(get_container),
) -> Callable[[], UnitOfWork]:
    """Фабрика, а не открытый unit of work: use case входа сам решает, когда
    открыть транзакцию, — например, после ответа провайдера."""
    return container.unit_of_work


def get_untraced_unit_of_work_factory(
    container: Container = Depends(get_container),
) -> Callable[[], UnitOfWork]:
    """Фабрика единиц работы без спанов SQL — для опроса в потоке событий GET /events.

    Опрос идёт раз в секунду всё время жизни соединения: спаны на каждый запрос
    раздули бы трейс запроса. Соединение берётся на одно чтение и сразу
    возвращается: поток живёт часами, и несколько открытых вкладок иначе исчерпали бы пул.
    """
    return container.untraced_unit_of_work


def get_oauth_provider(
    provider: Annotated[str, Path(description="Провайдер входа, например github.")],
    container: Container = Depends(get_container),
) -> OAuthProvider | None:
    """Провайдер входа по `{provider}` из пути; неизвестный — `None`."""
    return container.oauth_provider(provider)


def get_run_changes(container: Container = Depends(get_container)) -> RunChanges:
    """Сигнал изменений прогонов для потока событий; тесты подменяют его своим."""
    return container.run_changes()


def get_clock() -> Callable[[], float]:
    """Монотонные часы потока событий; тесты подменяют их ходом своего «времени»."""
    return time.monotonic


def get_cabinet_vcs_gateway(container: Container = Depends(get_container)) -> VcsGateway:
    """VCS-шлюз чтений кабинета (дифф прогона): свои тайм-ауты, не шлюз вебхука; тесты подменяют его фейком."""
    return container.cabinet_vcs_gateway()


def get_run_diff_cache(container: Container = Depends(get_container)) -> CacheStore:
    """Кэш диффов прогонов по паре коммитов — отдельный от кэша видимости."""
    return container.run_diff_cache()


def get_rules_cache(container: Container = Depends(get_container)) -> CacheStore:
    """Кэш исходов загрузки `.review/rules.md` — `cache_store()` процесса API; ключ и формат те же,
    что у прогона, но кэш процессный: записи воркера здесь не видны (design D7, Non-Goals)."""
    return container.cache_store()


def get_settings(container: Container = Depends(get_container)) -> Settings:
    """Настройки процесса, с которыми собран контейнер."""
    return container.settings
