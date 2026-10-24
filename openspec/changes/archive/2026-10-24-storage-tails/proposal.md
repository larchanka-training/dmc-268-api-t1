## Why

Три хвоста слоя хранения (задача #151). Поиск опубликованного комментария по `provider_comment_id` идёт без индекса: в `published_comments` есть только `uq_published_comments_finding`. `ContextPayloadRepo.add` кладёт в строку ссылку на `body` сущности, а не копию. Список прогонов считает `COUNT` и страницу двумя запросами в READ COMMITTED, и прогон, закоммиченный между ними, попадает на страницу, но не в `total`.

## What Changes

- Миграция 0011: `UNIQUE (provider_comment_id, kind)` на `published_comments`: у сводки id ревью GitHub, у комментария к строке - свой счётчик, они могут совпасть. Колонки `NOT NULL`, поэтому `NULLS NOT DISTINCT` не нужен. Дубль id комментария у провайдера - ошибка записи. Ограничение даёт и индекс для поиска по id.
- `SqlAlchemyContextPayloadRepo.add` сохраняет `copy.deepcopy(payload.body)`: `body` - вложенный `dict[str, Any]`.
- `SqlAlchemyReviewRunRepo.list_page` выполняет `COUNT` и страницу в одной транзакции REPEATABLE READ.

## Capabilities

### Modified Capabilities

- `review-data-model`: ADDED «Идентификатор комментария у провайдера уникален».

## Impact

- Код: `app/infrastructure/db/models.py`, `app/infrastructure/db/repositories.py`, миграция `alembic/versions/0011_*`.
- Схема: новое ограничение. На базе, где уже есть два комментария с одним id, `upgrade` упадёт; такие строки перед накатом нужно разобрать вручную.
- Контракт API не меняется. Документация: `docs/erd.md`.
