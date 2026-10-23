"""Заголовок `Authorization: Bearer` из `Secret` — через `httpx.Auth`.

Токен раскрывается только внутри потока аутентификации httpx, в момент
отправки: в локальных переменных вызывающего кода (и в трейсбеке с ними)
открытого значения нет.
"""

from collections.abc import Generator

import httpx

from app.domain.entities import Secret


class BearerAuth(httpx.Auth):
    def __init__(self, token: Secret) -> None:
        self._token = token

    def auth_flow(self, request: httpx.Request) -> Generator[httpx.Request, httpx.Response]:
        request.headers["Authorization"] = f"Bearer {self._token.reveal()}"
        yield request
