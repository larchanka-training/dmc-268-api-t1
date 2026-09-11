"""FastAPI wiring.

Routers ask for a port-typed dependency and never name a concrete adapter.
"""

from collections.abc import Iterator

from fastapi import Request

from app.application.ports import UnitOfWork
from app.infrastructure.container import Container


def get_container(request: Request) -> Container:
    return request.app.state.container


def get_unit_of_work(request: Request) -> Iterator[UnitOfWork]:
    with get_container(request).unit_of_work() as uow:
        yield uow
