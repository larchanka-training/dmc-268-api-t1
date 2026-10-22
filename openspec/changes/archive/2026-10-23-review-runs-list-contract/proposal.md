## Why

В кабинете нет списка прогонов ревью: в контракте есть только `GET /api/v1/reviews/{review_run_id}`. Вкладка `/app/reviews` (dmc-268-ui-t1#43) показывает снимок списка и обновляет его из общего потока изменений. Порядок задачи #103 — «контракт первым»: ручки заводятся заглушками `501 NOT_IMPLEMENTED` с полными схемами, фронтенд синхронизирует типы (`pnpm api:sync`) и делает экран параллельно с реализацией.

## What Changes

- `GET /api/v1/reviews` — заглушка списка прогонов (`ReviewRunListResponse`: `items` из `ReviewRunListItem` и `total`). Фильтры: `status` (несколько значений), `active`, `repository_id`, `author`, `q`, `created_from` / `created_to`; пагинация `limit` / `offset`. Строка — поля прогона, число находок и существующий `MergeRequestSummary`.
- `GET /api/v1/reviews/events` — заглушка общего потока Server-Sent Events: `run.created` (`ReviewRunListItem`), `run.status_changed` (`ReviewRunStatusChangedEvent`), `run.finished` (`ReviewRunFinishedEvent`), `ping`. Модели событий — в `components/schemas`.
- Обе требуют сессию и отвечают `501`; параметры списка проверяются раньше `501`, `created_from` позже `created_to` — `422`.
- В описании ручек — будущее поведение: видимость по доступу на GitHub (решение А в dmc-268-ui-t1#43), события в момент коммита (`NOTIFY`), ресинхронизация списком после обрыва.

Вне этого change: запросы к базе, правило видимости, `NOTIFY` / `LISTEN` — следующие шаги задачи #103.

## Impact

- Код: `app/api/reviews.py`, `app/api/schemas.py` (заголовки потока SSE в схеме — общие с `app/api/review_events.py`), `app/api/errors.py` (причина «начало диапазона позже конца»), `app/api/factory.py` (модели событий в `extra_models`).
- Контракт: `docs/openapi/openapi.json` — две операции и четыре схемы; существующие не меняются.
- Тесты: `tests/test_problem_details.py`, `tests/test_reviews_api.py`.
