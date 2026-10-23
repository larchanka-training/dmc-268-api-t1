## 1. Контракт

- [x] 1.1 `app/api/events.py`: заглушка `GET /api/v1/events` без параметров с сессией, ответом `text/event-stream` с `x-sse-events` и `501`; enum `StreamEventType`
- [x] 1.2 `app/api/reviews.py`: удалены заглушка `/reviews/events`, `ReviewRunsStreamEventType` и комментарий о порядке маршрутов; `last_progress_at` в `ReviewRunListItem`; описание `GET /reviews` ссылается на `GET /events`; хелпер `review_run_list_item`
- [x] 1.3 `app/api/factory.py`: роутер потока подключён, `StreamEventType` в `extra_models`

## 2. Тесты и проверки

- [x] 2.1 Тесты: `401` без сессии, `501` с сессией, контракт потока, `last_progress_at` в обязательных, `/reviews/events` — `422` проверки id прогона
- [x] 2.2 Экспорт `docs/openapi/openapi.json`; `ruff check`, `mypy`, `lint-imports`, `pytest`, `export_openapi.py --check`, `openspec validate --all --strict` проходят
