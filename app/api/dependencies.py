"""Проводка FastAPI.

Роутеры просят зависимость, типизированную портом, и никогда не называют
конкретный адаптер.
"""

import asyncio
import time
from collections.abc import Awaitable, Callable, Iterator
from typing import Annotated, cast
from uuid import UUID

from fastapi import Depends, Path, Request

from app.application.ports import UnitOfWork
from app.application.ports.installation_catalog import InstallationCatalog
from app.application.ports.job_queue import JobQueue
from app.application.ports.oauth_provider import OAuthProvider
from app.domain.entities import ReviewRun
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


def get_oauth_provider(
    provider: Annotated[str, Path(description="Провайдер входа, например github.")],
    container: Container = Depends(get_container),
) -> OAuthProvider | None:
    """Провайдер входа по `{provider}` из пути; неизвестный — `None`."""
    return container.oauth_provider(provider)


def get_run_reader(
    container: Container = Depends(get_container),
) -> Callable[[UUID], ReviewRun | None]:
    """Чтение прогона короткой единицей работы — для потока событий.

    `get_unit_of_work` держит транзакцию и соединение до конца запроса, а
    поток живёт минуты: несколько открытых вкладок исчерпали бы пул. Здесь
    соединение берётся на одно чтение и сразу возвращается.
    """

    def read(run_id: UUID) -> ReviewRun | None:
        with container.unit_of_work() as uow:
            return uow.review_runs.get(run_id)

    return read


def get_sleep() -> Callable[[float], Awaitable[None]]:
    """Ожидание между проверками статуса; тесты подменяют его мгновенным."""
    return asyncio.sleep


def get_clock() -> Callable[[], float]:
    """Монотонные часы потока событий; тесты подменяют их ходом своего «времени»."""
    return time.monotonic
