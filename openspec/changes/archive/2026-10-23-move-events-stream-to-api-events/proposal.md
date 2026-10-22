## Why

Поток изменений прогонов `GET /api/v1/reviews/events` (PR #105, задача #103) привязан к одному ресурсу. Нужен один поток на весь кабинет: сейчас для прогонов, потом для новостей и других ресурсов; путь под `/reviews` для этого не подходит. Этот поток заменит поток одного прогона `GET /reviews/{review_run_id}/events`. Экрану прогона для предупреждения «прогон давно не продвигался» нужно `last_progress_at`, которого в строке списка нет.

## What Changes

- **BREAKING** Заглушка потока переезжает с `GET /api/v1/reviews/events` на `GET /api/v1/events`, без параметров; старый путь удалён (клиентов у заглушки нет).
- Имя события — `<ресурс>.<действие>`, данные — модель своего типа в `x-sse-events`; клиент игнорирует незнакомые типы. Видимость задаёт правило типа события; для `run.*` — доступ к репозиторию у провайдера.
- Enum имён событий `ReviewRunsStreamEventType` переименован в `StreamEventType`, значения те же.
- В `ReviewRunListItem` добавлено обязательное `last_progress_at`.
- Описание `GET /reviews` ссылается на `GET /events`; поток одного прогона не меняется.

## Impact

- Код: новый `app/api/events.py`, `app/api/reviews.py` (`review_run_list_item` собирает строку списка для будущей реализации, `GET /reviews/{review_run_id}` строит запрос на изменение тем же хелпером), `app/api/factory.py`.
- Контракт: `docs/openapi/openapi.json` — новый путь, нет `/reviews/events`, компонент `StreamEventType` вместо `ReviewRunsStreamEventType`, поле `last_progress_at` у `ReviewRunListItem`. Фронтенд синхронизирует типы заново.
- Тесты: `tests/test_problem_details.py`, `tests/test_reviews_api.py`.
