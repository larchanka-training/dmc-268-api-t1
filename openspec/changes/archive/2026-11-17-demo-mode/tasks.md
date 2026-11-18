## 1. Ядро

- [x] 1.1 Константы демо (`DEMO_PROVIDER_USER_ID`, префикс `demo-`, `DEMO_SESSION_ID`) и чистые функции `is_demo_user(user)`, `is_demo_repository(repository)`, `demo_installation_repository` в `app/domain/demo.py`; юнит-тесты `tests/domain/test_demo.py`
- [x] 1.2 Условие демо-входа — `GET` без действительной сессии при включённом режиме — прямо в `current_session`; ветки покрыты API-тестами `tests/test_demo_mode_api.py`
- [x] 1.3 Настройка `demo_mode: bool = False` в `app/config.py` (`DEMO_MODE`)

## 2. Адаптеры и ручки

- [x] 2.1 Демо-пользователь и демо-репозитории — существующими `users.find_by_provider` и `repositories.list_paid_by` (новых методов адаптеров нет); use case `demo_sign_in` в `app/application/use_cases/auth.py`
- [x] 2.2 `current_session` (`app/api/session.py`): при `GET` без сессии в демо-режиме подставить `SignedIn` демо-пользователя; недействительная cookie удаляется и в демо
- [x] 2.3 Use case `list_user_repositories` (`UserContext.demo`): для демо-пользователя — демо-репозитории из базы без `InstallationCatalog`/`OAuthProvider`; правила демо-репозитория — `absent` без GitHub, `install_url` демо — `null`; поток событий демо не закрывается проверкой сессии
- [x] 2.4 `is_demo` в модели ответа `GET /api/v1/auth/me` (`app/api/auth.py`), `Field(description=…)`
- [x] 2.5 Тесты API `tests/test_demo_mode_api.py`: анонимный `GET` /reviews, карточка, шаги, дифф, /repositories с карточкой, настройками, правилами и PR, аналитика, поток событий в демо → без вызова GitHub (`FakeInstallationCatalog`); `POST /reviews` → `401`; вошедший пользователь не видит демо-прогонов; без `DEMO_MODE` и без seed — `401`
- [x] 2.6 `uv run python scripts/export_openapi.py`, файл в тот же коммит

## 3. Seed

- [x] 3.1 `demo_dataset(now)` и `seed_demo(uow, dataset)` в `app/application/use_cases/seed_demo.py`: демо-пользователь, 2 репозитория, PR, прогоны `completed`/`failed`/`analysing` с шагами и находками через unit of work; CLI `app/worker/seed_demo.py` выходит 0 без `DEMO_MODE=true`
- [x] 3.2 Интеграционный тест `tests/db/test_seed_demo.py`: двойной запуск даёт те же строки; чужие данные не тронуты
- [x] 3.3 Seed в образе: CLI в `app/worker`, как `credit_balance` — правка `Dockerfile` не нужна

## 4. Запуск и деплой

- [x] 4.1 `docker-compose.yml`: сервис `seed` после `migrate`, `DEMO_MODE` у `api`; `.env.example`: `DEMO_MODE=true` с комментарием
- [x] 4.2 `compose-smoke` в `.github/workflows/ci.yml`: анонимный `GET :8080/api/v1/reviews` возвращает демо-прогон
- [x] 4.3 `infra/` не передаёт `DEMO_MODE` приложению стенда; тест `tests/test_demo_mode_deploy.py`
- [x] 4.4 `README.md`, «Самый короткий путь»: что видно без App и как войти по-настоящему
- [x] 4.5 Спеки синхронизированы, change заархивирован
