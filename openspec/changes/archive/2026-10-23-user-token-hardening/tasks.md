# Tasks

- [x] 1.1 `call_with_user_token`: на `ProviderTokenRejected` перечитать токен и повторить один раз с новым; тесты: повтор после параллельного refresh, повтор тоже `401` — сессия закрыта
- [x] 1.2 Чистая `is_usable(token, now)`; сбой обмена и `ProviderTokenBusy` при действующем токене — прежний токен; тесты
- [x] 1.3 `SqlAlchemyProviderTokenRepo.get_for_update`: `SET LOCAL lock_timeout = '15s'`, `LockNotAvailable` → `ProviderTokenBusy`; интеграционный тест
- [x] 1.4 `httpx.Timeout(10, connect=5)` для клиентов OAuth и каталога
- [x] 1.5 `GitHubInstallationCatalog`: `403` на установке — пропуск, `403` лимита — `VcsUnavailableError`, пагинация до `total_count` и не больше 50 страниц, токен через `BearerAuth`; тесты
- [x] 1.6 `OAuthProvider.revoke` и `GitHubOAuthProvider.revoke`; `logout` отдаёт токен, ручка отзывает его фоновой задачей через `revoke_quietly`; тесты адаптера, use case и API
- [x] 1.7 Экспорт `docs/openapi/openapi.json`, `docs/BACKEND_ARCHITECTURE.md`, design D4 change `repositories-via-user-token`
- [x] 1.8 `ruff`, `lint-imports`, `mypy`, `pytest` с базой, `export_openapi.py --check`, `openspec validate --all --strict`, `alembic heads`
