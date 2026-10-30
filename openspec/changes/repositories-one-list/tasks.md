## 1. Контракт и один список

- [x] 1.1 Slug App из `GET /app`: `GitHubVcsGateway.app_slug` (общий кэш с `bot_login`), `Container.app_slug` (`None` без ключа и при сбое), зависимость `get_app_slug` (`app/api/dependencies.py`)
- [x] 1.2 Use case `list_with_connections` (`app/application/use_cases/connect_repository.py`): строки из ответа GitHub, запись сервиса (`record`) из базы, `can_connect`, порядок по `full_name` без учёта регистра; `list_available_repositories` и `AvailableRepository` удалены
- [x] 1.3 `GET /api/v1/repositories` (`app/api/repositories.py`): `RepositoryListItem`, `install_url` из slug в теле ручки, пагинация срезом, кэш видимости, `401`/`502`
- [x] 1.4 `GET /api/v1/repositories/available`, её модели, зависимость `get_refreshing_catalog` и тесты удалены; описания `GET /events`, `GET /reviews` и `docs/BACKEND_ARCHITECTURE.md` ссылаются на `GET /repositories`
- [x] 1.5 Заглушка `DELETE /api/v1/repositories/{repository_id}` с полной схемой (`204`, `403`, `404`, `422`, `501`, `502`); описание `POST` — повторное включение и `409` как успех
- [x] 1.6 Заглушка Setup URL `GET /api/v1/auth/github/setup` (`app/api/auth.py`) с параметрами и редиректом в схеме; без сессии — `302` на `/login?redirect=/app/repositories`
- [x] 1.7 Тесты: состав и поля строк, порядок и страницы, `install_url` — slug известен, ключа нет, сбой `GET /app`; отсутствие `/available`, `501` и проверки до него у новых ручек, вход без сессии у Setup URL
- [x] 1.8 `README.md`, «Регистрация репозитория»; экспорт `docs/openapi/openapi.json` (`uv run python scripts/export_openapi.py`)

## 2. Мягкое выключение

- [x] 2.1 Миграция: `repositories.connected_at timestamptz NULL`, заполнение из `created_at`, откат; поле в `Repository` и адаптере
- [x] 2.2 Use case списка: подключённым считается запись с `connected_at`, `connected_at` строки — из колонки
- [x] 2.3 Use case `disconnect_repository` и адаптер `RepositoryRepository.disconnect`; `DELETE /repositories/{id}` вместо заглушки
- [x] 2.4 Повторное включение в `connect_repository`: `UPDATE … WHERE connected_at IS NULL RETURNING`, затем вставка; интеграционный тест гонки
- [x] 2.5 Приём вебхука: выключенный репозиторий — `202 ignored` без прогона, как незарегистрированный, и check run `success` «Ревью выключено в сервисе» на head (best-effort)

## 3. Setup URL

- [x] 3.1 Порт и адаптер: установки пользователя с `repository_selection` (`GET /user/installations`) и репозитории одной установки
- [x] 3.2 Чистая функция выбора репозиториев к подключению (selected, admin или maintain, без записи) и use case возврата с установки
- [x] 3.3 Ручка вместо заглушки: сверка установки, свежий ответ в кэш (`read=False`), редирект с итогом; `state` в `install_url`, привязанный к сессии, и его сверка на Setup URL (design D7)
- [ ] 3.4 Проверить на стенде, что GitHub возвращает `state` на Setup URL, и только после этого прописать Setup URL в настройках GitHub App (design D7)

Заметка к 3.2: `connect_repository` после шага 2 включает выключенные обратно (`reconnect`), а
Setup URL выключенные включать не должен (design D7, п. 2). Поэтому Setup URL зовёт отдельный
`connect_installation`: только вставка, выбор — `to_connect_on_setup`.

## 4. Завершение

- [x] 4.1 `docs/GITHUB_APP.md` (PR #244): «Планируется» привести к сделанному
- [ ] 4.2 Экспорт контракта, синхронизация спек и `openspec archive repositories-one-list`
