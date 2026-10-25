# Proposal

## Why

При выкате новой версии API простаивает больше минуты (#159). `docker_image.api` общий для `migrate`, `api` и `worker`, а замена ресурса образа ждёт уничтожения всех его контейнеров, включая дренаж воркера (`destroy_grace_seconds` до 41 мин). Замер на локальном Docker с дренажом воркера 60 с: 64 с простоя. Один вынос миграций из стека не помогает — образ остаётся общим.

## What Changes

- Миграции — шаг `deploy.yml` между `tofu apply -target` базы и брокера и полным `tofu apply`: `.github/scripts/deploy/migrate.sh` запускает `docker run --rm` образа выката с `alembic upgrade head` через `DOCKER_HOST=ssh://stand`. Окружение — sensitive-выход `migrate_env` из того же `local.app_env`, что у `api` и `worker`, через env-файл с правами 600.
- Контейнер `docker_container.migrate` уходит из стека блоком `removed` без уничтожения; остановленный `dmc268-migrate` удаляет скрипт миграций. Уходят постусловие миграций, `-replace` в шаге apply и тест `failed_migration_stops_deploy`.
- Отдельный `docker_image.worker` с той же ссылкой: замена образа API не ждёт дренажа воркера.
- Чистый сервер: `apply -target` сети, базы и брокера на каждом деплое; у postgres `wait = true`, цикл повторов в миграциях не нужен.
- Вход в `ghcr.io` на раннере (`docker/login-action`): docker CLI передаёт учётные данные демону стенда при pull.
- Expand/contract в скилле `alembic-migration` и в `docs/BACKEND_ARCHITECTURE.md`.

**Не входит:** `pg_isready -h 127.0.0.1` у postgres (закрывает окно временного сервера инициализации на пустом томе) — смена healthcheck пересоздаёт базу, а без `destroy_grace_seconds` это SIGKILL; вместе с #102.

## Capabilities

### Modified Capabilities

- `backend-delivery`: миграции — шаг развёртывания до выката нового кода, окружение миграций из того же описания, простой API не зависит от дренажа воркера.
- `database-migrations`: миграция совместима с работающей версией кода, ломающее изменение — в два деплоя.

## Impact

- `infra/app.tf`, `infra/locals.tf`, `infra/postgres.tf`, `infra/outputs.tf` (новый), `infra/tests/`, `infra/README.md`.
- `.github/workflows/deploy.yml`, `.github/scripts/deploy/migrate.sh` (новый), `.github/scripts/deploy/check-api.sh`.
- `tests/test_stack_descriptions.py`, `.agents/skills/alembic-migration/SKILL.md`, `docs/BACKEND_ARCHITECTURE.md`.
- Первый деплой после мёржа: простой API ещё прежний (порядок уничтожения берётся из состояния), `postgres` обновляется на месте без перезапуска, `docker_container.migrate` снимается с учёта. Выигрыш — со второго деплоя.
