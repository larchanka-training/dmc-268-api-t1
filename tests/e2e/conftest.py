"""Общая проверка E2E: адаптер не ходил в GitHub туда, чего фейк не ждёт."""

from collections.abc import Iterator

import pytest


@pytest.fixture(autouse=True)
def _no_unexpected_github_requests(request: pytest.FixtureRequest) -> Iterator[None]:
    """Неожиданный запрос фейк отвечает `404` и записывает: best-effort шаги
    (check run, GraphQL) такой `404` переживают, и регрессия маршрута прошла бы молча."""
    yield
    if "stack" in request.fixturenames:
        assert request.getfixturevalue("stack").github.unexpected == []
