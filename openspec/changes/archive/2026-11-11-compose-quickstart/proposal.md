## Why

#386 (Sprint 5, DevOps): проект должен разворачиваться с нуля на чистой машине одной командой. `docker-compose.yml` описывает базу, брокер, миграции, API и воркер, но ни разу не проверялось, что `cp .env.example .env && docker compose up` с чистого клона действительно поднимает стек.

## What Changes

- Сервис `ui` в `docker-compose.yml`: образ кабинета собирается из публичного репозитория `dmc-268-ui-t1` (`#develop`; пакет в GHCR закрыт для анонимного скачивания), порт `127.0.0.1:8080`, nginx кабинета проксирует `/api/` на `dmc268-api`.
- Job CI `compose-smoke`: на чистом раннере `cp .env.example .env`, `docker compose up -d --wait`, `GET /health`, `GET /api/v1/status` через кабинет, воркер running; при сбое — состояние и логи сервисов. Раннер одноразовый, поэтому `docker compose down` не нужен.

## Impact

- `docker-compose.yml`, `.github/workflows/ci.yml`, `README.md`. Поведение сервиса и спеки не меняются.
