# Tasks

- [x] 1.1 Зависимость `available_repositories`: токен пользователя через `call_with_user_token`, `SessionClosed` — `401`, `VcsError` и `OAuthProviderUnavailable` — `502`; ручка `/available` и поток `/events` берут видимость из неё
- [x] 1.2 Тесты `tests/test_events_api.py`: сессия без токена, отозванный токен и отвергнутый refresh — `401` с удалением cookie и закрытой сессией; сбой GitHub при обмене — `502`, сессия жива; истёкший токен обменивается и поток открывается; проверка сессии в потоке не ходит в GitHub
- [x] 1.3 Описание `GET /api/v1/events` и `ERRORS_502_SCM_EVENTS`, экспорт `docs/openapi/openapi.json`
- [x] 1.4 `ruff`, `lint-imports`, `mypy`, `pytest` с базой, `export_openapi.py --check`, `openspec validate --all --strict`, `alembic heads`
