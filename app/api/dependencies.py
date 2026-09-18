"""FastAPI wiring.

Routers ask for a port-typed dependency and never name a concrete adapter.
"""

from collections.abc import Iterator
from typing import cast

from fastapi import Request

from app.application.ports import UnitOfWork
from app.infrastructure.container import Container


def get_container(request: Request) -> Container:
    # `Request.app.state` is Starlette's `State`, whose `__getattr__` is
    # typed to return `Any`; the composition root is the only writer of this
    # attribute (see `create_app`), so the cast closes the `Any` boundary
    # here rather than letting it leak into every caller of this dependency.
    return cast(Container, request.app.state.container)


def get_unit_of_work(request: Request) -> Iterator[UnitOfWork]:
    with get_container(request).unit_of_work() as uow:
        yield uow
