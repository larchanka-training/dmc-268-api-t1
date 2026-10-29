## Why

Подключение репозитория сейчас разнесено на две страницы и две ручки: «Подключение репозитория»
(`GET /repositories/available`) и «Репозитории» (`GET /repositories`, только подключённые).
Поставить приложение из сервиса нельзя, выключить ревью — тоже: ручки удаления нет, а флаг
`auto_review_enabled` настраивается только через ручки настроек, которые отвечают `501`.
Пользователь не может сам поставить приложение на свой приватный репозиторий и включать и
выключать в нём ревью (dmc-268-ui-t1#95, бэкенд-часть — #243). Схема согласована в
`docs/GITHUB_APP.md` (PR #244), разделы «Подключение → Планируется» и «Отключение».

## What Changes

- **BREAKING** `GET /api/v1/repositories` — один список: все репозитории установок, видимые
  пользователю по его токену, подключённые и нет. Строка — `provider`, `provider_id`,
  `full_name`, `default_branch`, `connected`, `id` и `connected_at` (только у подключённых,
  иначе `null`), `can_connect`; у ответа — `items` и `install_url`
  (`https://github.com/apps/<slug>/installations/new`, slug — настройка `GITHUB_APP_SLUG`).
  Постранично, по имени; кэш видимости 30 с остаётся. `auto_review_enabled` в строке нет.
- **BREAKING** `GET /api/v1/repositories/available` удаляется вместе с `AvailableRepositoryResponse`
  и `AvailableRepositoryListResponse`.
- `DELETE /api/v1/repositories/{repository_id}` — выключить ревью: мягко, запись остаётся,
  история сохраняется; уже выключен — успех. Права — как у `POST`.
- `POST /api/v1/repositories` на выключенный репозиторий включает его обратно с прежним `id`;
  `409` — только на включённый, интерфейс считает его успехом.
- Setup URL `GET /api/v1/auth/github/setup?installation_id=…&setup_action=…`: сверяет установку
  с токеном пользователя, при «Only select repositories» подключает репозитории, где у
  пользователя admin или maintain, при «All repositories» — ничего; редирект на «Репозитории»
  с итогом. Сбрасывает кэш видимости свежим ответом GitHub.
- Вебхук по выключенному репозиторию игнорируется так же, как по неподключённому.
- Вебхуки `installation` (`deleted`, `suspend`) и `installation_repositories` (`removed`)
  выключают ревью у затронутых репозиториев.
- Контракт первым: в этом шаге `DELETE` и Setup URL — заглушки `501` с полной схемой,
  список — реализован.

## Capabilities

### New Capabilities

### Modified Capabilities

- `review-rest-api`: один список репозиториев вместо двух, `install_url`, выключение ревью,
  повторное включение, Setup URL, кэш видимости без страницы подключения.
- `webhook-intake`: выключенный репозиторий игнорируется; снятие приложения на GitHub
  выключает ревью.
- `review-data-model`: регистрация репозитория хранит, включено ли ревью, и переживает
  выключение.

## Impact

- `app/api/repositories.py`, `app/api/auth.py`, `app/api/dependencies.py`, `app/api/errors.py`,
  `app/application/use_cases/connect_repository.py`, `app/config.py`; контракт
  `docs/openapi/openapi.json`.
- Окружение: новая настройка `GITHUB_APP_SLUG` — `.env.example`, `docker-compose.yml`,
  `infra/` (переменная `github_app_slug`), `deploy.yml` (переменная репозитория
  `APP_SLUG_DMC268_T1`).
- Фронтенд (dmc-268-ui-t1#96): страница «Подключение репозитория» и её ручка уходят, типы
  списка меняются.
- Следующий шаг: миграция `repositories.connected_at`, реализация `DELETE`, повторного
  включения, Setup URL и вебхуков `installation*`.
