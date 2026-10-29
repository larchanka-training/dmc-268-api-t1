## 1. Контракт и один список

- [x] 1.1 Настройка `GITHUB_APP_SLUG` (`app/config.py`) с проверкой формата; зависимость `get_install_url` (`app/api/dependencies.py`); окружение — `.env.example`, `docker-compose.yml`, `infra/` (`github_app_slug`), `deploy.yml` (`vars.APP_SLUG_DMC268_T1`), `infra/README.md`
- [x] 1.2 Use case `list_repositories` (`app/application/use_cases/connect_repository.py`): строки из ответа GitHub, запись сервиса из базы, `can_connect`, порядок по `full_name` без учёта регистра и `provider_id`; `list_available_repositories` и `AvailableRepository` удалены
- [x] 1.3 `GET /api/v1/repositories` (`app/api/repositories.py`): `RepositoryListItem`, `install_url`, пагинация срезом, кэш видимости, `401`/`502`
- [x] 1.4 `GET /api/v1/repositories/available`, её модели, зависимость `get_refreshing_catalog` и тесты удалены; описания `GET /events`, `GET /reviews` и `docs/BACKEND_ARCHITECTURE.md` ссылаются на `GET /repositories`
- [x] 1.5 Заглушка `DELETE /api/v1/repositories/{repository_id}` с полной схемой (`204`, `403`, `404`, `422`, `501`, `502`); описание `POST` — повторное включение и `409` как успех
- [x] 1.6 Заглушка Setup URL `GET /api/v1/auth/github/setup` (`app/api/auth.py`) с параметрами и редиректом в схеме
- [x] 1.7 Тесты: состав и поля строк, порядок и страницы, `install_url` и `null`, отсутствие `/available`, `501` и проверки до него у новых ручек, формат slug
- [x] 1.8 `README.md`, «Регистрация репозитория»; экспорт `docs/openapi/openapi.json` (`uv run python scripts/export_openapi.py`)

## 2. Мягкое выключение

- [ ] 2.1 Миграция: `repositories.connected_at timestamptz NULL`, заполнение из `created_at`, откат; поле в `Repository` и адаптере
- [ ] 2.2 Use case списка: подключённым считается запись с `connected_at`, `connected_at` строки — из колонки
- [ ] 2.3 Use case `disconnect_repository` и адаптер `RepositoryRepository.disconnect`; `DELETE /repositories/{id}` вместо заглушки
- [ ] 2.4 Повторное включение в `connect_repository`: `UPDATE … WHERE connected_at IS NULL RETURNING`, затем вставка; интеграционный тест гонки
- [ ] 2.5 Приём вебхука: выключенный репозиторий — `202 ignored`, как незарегистрированный

## 3. Setup URL

- [ ] 3.1 Порт и адаптер: установки пользователя с `repository_selection` (`GET /user/installations`) и репозитории одной установки
- [ ] 3.2 Чистая функция выбора репозиториев к подключению (selected, admin или maintain, без записи) и use case возврата с установки
- [ ] 3.3 Ручка вместо заглушки: сверка установки, свежий ответ в кэш (`read=False`), редирект с итогом; проверить запуском, приходит ли `state` на Setup URL (design D7)

## 4. Вебхуки снятия приложения

- [ ] 4.1 `installation_repositories.removed` выключает репозитории из `repositories_removed`
- [ ] 4.2 `installation.deleted` и `installation.suspend` выключают репозитории владельца установки

## 5. Завершение

- [ ] 5.1 `docs/GITHUB_APP.md` (PR #244): «Планируется» привести к сделанному
- [ ] 5.2 Экспорт контракта, синхронизация спек и `openspec archive repositories-one-list`
