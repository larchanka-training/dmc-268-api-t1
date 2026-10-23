# Tasks

## 1. Ядро

- [x] 1.1 `Secret`, `ProviderGrant`, `ProviderToken`, `OAuthLogin`, `RepositoryPermissions` в `app/domain/entities.py`; удалить `RepositoryRole`
- [x] 1.2 Чистые `token_from_grant`, `needs_refresh`, `can_refresh` и исключения `ProviderTokenRejected`, `ProviderTokenUnavailable` в `app/domain/provider_token.py` с тестами
- [x] 1.3 Чистая `can_connect(permissions)` в `app/domain/repository_access.py` с тестом; `can_see` удалить
- [x] 1.4 Порты: `ProviderTokenRepo` (`get`, `get_for_update`, `save`), `OAuthProvider.exchange_code → OAuthLogin` и `refresh`, `InstallationCatalog.list_repositories(access_token)`
- [x] 1.5 Use case `call_with_user_token` (`app/application/use_cases/user_token.py`): refresh под блокировкой, закрытие сессии; тесты: свежий, refresh, без истечения, отказ refresh, истёкший refresh token, сбой GitHub, нет токена, нечитаемый, гонка, `401` на токен, токены не в логе
- [x] 1.6 `complete_login` сохраняет токен в транзакции сессии, без ключа — отказ входа; `authenticate` отдаёт `SignedIn` с id сессии
- [x] 1.7 `list_available_repositories` и `connect_repository` по токену пользователя; тесты: невидимый = отсутствующий (`404`), без admin/maintain — `403`, maintain подключает, `409`, `201`

## 2. Адаптеры

- [x] 2.1 `TokenCipher` (AES-256-GCM, associated data — сессия и поле) с тестами: туда-обратно, чужой ключ, чужая строка и поле, мусор
- [x] 2.2 `ProviderTokenRow`, `SqlAlchemyProviderTokenRepo`, миграция `0009_add_provider_tokens.py` с откатом; интеграционные тесты: шифротекст в таблице, оба режима, замена, без ключа и с чужим ключом, каскад выхода и уборки, гонка двух refresh, откат миграции
- [x] 2.3 `GitHubOAuthProvider`: токен и сроки из обмена `code`, `refresh` (`bad_refresh_token` — отказ, прочее — недоступен); тесты на формах из документации
- [x] 2.4 `GitHubInstallationCatalog` через `/user/installations…`: пагинация, приостановленные и исчезнувшие установки, `401`, `502` на неожиданное тело; тесты на формах из документации с указанием источника; старые запросы удалить
- [x] 2.5 Настройка `USER_TOKEN_ENCRYPTION_KEY` с проверкой формата; контейнер собирает `TokenCipher` и каталог без App-реквизитов

## 3. HTTP, деплой и документы

- [x] 3.1 `current_session`; ручки репозиториев: `SessionClosed` — `401` с удалением cookie, `OAuthProviderUnavailable` — `502`; описания ручек; API-тесты, в том числе токен не в ответе и не в логах входа
- [x] 3.2 Экспорт `docs/openapi/openapi.json` (`uv run python scripts/export_openapi.py`)
- [x] 3.3 `infra/` (переменная с проверкой формата, окружение API), `deploy.yml` (`USER_TOKEN_KEY_DMC268_T1`), `docker-compose.yml`, `.env.example`
- [x] 3.4 README, `docs/BACKEND_ARCHITECTURE.md`, `docs/erd.md`
- [x] 3.5 `ruff check`, `lint-imports`, `mypy`, `pytest` с базой, `export_openapi.py --check`, `openspec validate --all --strict`, `alembic heads`, откат миграции проходят
