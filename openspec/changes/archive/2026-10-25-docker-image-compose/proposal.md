## Why

Образ собирается в один этап: uv и его кэш остаются в рантайме, а байткод `app` и зависимостей компилируется при каждом старте (на стенде ФС только для чтения, так что впустую). `.dockerignore` исключает `__pycache__` и `*.pyc` только в корне контекста, и файлы разработчика из `app/` уезжают в образ. В `docker-compose.yml` порты базы, Redis, брокера и API открыты на всех интерфейсах, миграции идут в команде API при каждом старте, Redis поднимается всегда, хотя ему нет потребителей. `.env.example` указывает учётку брокера, которой в compose нет. Образ и Dockerfile не проверяются в CI.

## What Changes

- Dockerfile: multi-stage по гайду uv для Docker. Сборка с uv в отдельном этапе (`UV_COMPILE_BYTECODE=1`), рантайм без uv копирует готовый `.venv`; байткод `app` и `alembic` компилируется при сборке; pip из базового образа удаляется. Пользователь не root, команда по умолчанию и `python -m app.worker` / `alembic` прежние.
- `.dockerignore`: `**/__pycache__`, `**/*.pyc`, а также каталоги, которых нет в `COPY` (тесты, документация, OpenSpec, скрипты).
- `docker-compose.yml`: порты на `127.0.0.1`; одноразовый сервис `migrate` (`alembic upgrade head`), API и воркер ждут его через `service_completed_successfully`; миграций в команде API больше нет; Redis за профилем `redis`.
- `.env.example`: учётка брокера как в compose.
- CI: job `image` — hadolint по Dockerfile и trivy по собранному образу (HIGH, CRITICAL, `ignore-unfixed`).
- README: раздел запуска.

## Impact

- `Dockerfile`, `.dockerignore`, `docker-compose.yml`, `.env.example`, `.github/workflows/ci.yml`, `README.md`. Код приложения, API и контракт не меняются; `infra/` не затрагивается.
