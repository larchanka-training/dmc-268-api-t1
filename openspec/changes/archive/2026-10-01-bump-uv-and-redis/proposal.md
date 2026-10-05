# Proposal

## Why

- **uv** закреплён на 0.12.12 в CI и в образе, актуальная — 0.12.21. Девять патч-релизов отставания; у части команды локально уже новее (0.12.19), и расхождение будет только расти.
- **Redis** в локальном стеке — `7-alpine`, актуальная мажорная версия — 8.

## What Changes

- `setup-uv` в трёх джобах `ci.yml` и `COPY --from=ghcr.io/astral-sh/uv` в `Dockerfile`: 0.12.21.
- `docker-compose.yml`: `redis:8-alpine`.

**Не входит:** обновление Python-зависимостей в `uv.lock` — отдельная ветка `deps-update-…`.

## Capabilities

### New Capabilities

Нет.

### Modified Capabilities

Нет.

`skip_specs: true`: меняются версии инструмента и образа локального стека, а не поведение сервиса.

## Impact

- `.github/workflows/ci.yml`, `Dockerfile`, `docker-compose.yml`.
- Redis есть только в `docker-compose.yml`, приложение к нему пока не обращается; на стенде Redis нет. Данные локального Redis 7 тому 8 не нужны: у сервиса нет тома.
- Redis 8 распространяется под AGPLv3 наравне с прежними RSALv2/SSPLv1; для локального стека разработки это ничего не меняет.
