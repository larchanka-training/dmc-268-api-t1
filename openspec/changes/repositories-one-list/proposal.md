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
  `full_name`, `default_branch`, `connected`, `id` (есть, если запись есть, в том числе у
  выключенного), `connected_at` (только у включённых), `can_connect`; у ответа — `items` и `install_url`
  (`https://github.com/apps/<slug>/installations/new`, slug — из `GET /app`, `null`, если не
  узнать). Постранично, по имени; кэш видимости 30 с остаётся. `auto_review_enabled` в строке нет.
- **BREAKING** `GET /api/v1/repositories/available` удаляется вместе с `AvailableRepositoryResponse`
  и `AvailableRepositoryListResponse`.
- `DELETE /api/v1/repositories/{repository_id}` — выключить ревью: мягко, запись остаётся,
  история сохраняется; уже выключен — успех. Права — как у `POST`.
- `POST /api/v1/repositories` на выключенный репозиторий включает его обратно с прежним `id`;
  `409` — только на включённый, интерфейс считает его успехом.
- Setup URL `GET /api/v1/auth/github/setup?installation_id=…&setup_action=…`: сверяет установку
  с токеном пользователя, после установки (`setup_action=install`) при «Only select
  repositories» подключает репозитории, где у пользователя admin или maintain; при «All
  repositories» и после изменения установки (`update`) — ничего; редирект на «Репозитории»
  с итогом. Сбрасывает кэш видимости свежим ответом GitHub.
- Вебхук по выключенному репозиторию игнорируется так же, как по неподключённому.
- Контракт первым: в этом шаге `DELETE` и Setup URL — заглушки `501` с полной схемой,
  список — реализован.

## Capabilities

### New Capabilities

### Modified Capabilities

- `review-rest-api`: один список репозиториев вместо двух, `install_url`, выключение ревью,
  повторное включение, Setup URL, кэш видимости без страницы подключения.
- `webhook-intake`: выключенный репозиторий игнорируется.
- `review-data-model`: регистрация репозитория хранит, включено ли ревью, и переживает
  выключение.

## Impact

- `app/api/repositories.py`, `app/api/auth.py`, `app/api/dependencies.py`, `app/api/errors.py`,
  `app/application/use_cases/connect_repository.py`, `app/config.py`; контракт
  `docs/openapi/openapi.json`.
- `app/infrastructure/vcs/github.py`, `app/infrastructure/container.py`: slug App из `GET /app`
  общий с логином бота. Окружение и `infra/` не меняются.
- Фронтенд (dmc-268-ui-t1#96): страница «Подключение репозитория» и её ручка уходят, типы
  списка меняются.
- Следующий шаг: миграция `repositories.connected_at`, реализация `DELETE`, повторного
  включения и Setup URL. Вебхуки `installation*` не обрабатываются (design D10).
- Порядок выката: фронт готовит PR на sha этой ветки, бэкенд мёржится, сразу за ним фронт;
  Setup URL в настройках GitHub App не прописывается, пока ручка отвечает `501`.
