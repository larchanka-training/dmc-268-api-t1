# Proposal

## Why

Change `add-rules-e2e-benchmark-and-load` (PR #74) добавил исход загрузки правил `empty`: файл разобран, но ни один раздел не стал правилом. Заглушка `GET /api/v1/repositories/{repository_id}/rules` (задача #113) описывает в контракте ровно исходы загрузки правил домена, и тест контракта требует совпадения. Без варианта `empty` фронтенд не узнает о таком статусе.

## What Changes

- В `RulesStatusResponse` пятый вариант `RulesEmpty`: `status = empty`, `branch`, `path`, `rejected`, `fetched_at`. Принятых правил у него нет.
- Ручка по-прежнему отвечает `501 NOT_IMPLEMENTED`.

## Capabilities

### Modified Capabilities

- `review-rest-api`: «Статус правил репозитория описан заглушкой».

## Impact

`app/api/repositories.py` (модель и описание ручки), `docs/openapi/openapi.json`, тест контракта. Фронтенд получит вариант после `pnpm api:sync`.
