## Why

#386 (Sprint 5, DevOps): проект должен разворачиваться с нуля на чистой машине одной командой. `docker-compose.yml` описывает базу, брокер, миграции, API и воркер, но ни разу не проверялось, что `cp .env.example .env && docker compose up` с чистого клона действительно поднимает стек.

## What Changes

- Job CI `compose-smoke`: на чистом раннере `cp .env.example .env`, `docker compose up -d --wait`, `GET /health`; при сбое — состояние и логи сервисов, в конце `docker compose down -v`.

## Impact

- `.github/workflows/ci.yml`. Поведение сервиса и спеки не меняются.
