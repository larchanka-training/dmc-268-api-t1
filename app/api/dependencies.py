"""Проводка FastAPI.

Роутеры просят зависимость, типизированную портом, и никогда не называют
конкретный адаптер.
"""

from collections.abc import Iterator
from typing import cast

from fastapi import Request

from app.application.ports import UnitOfWork
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
