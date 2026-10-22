## Why

`GET /api/v1/events` описан заглушкой `501` (задача #103, PR #115). Экран прогона во фронтенде переходит на общий поток с потока одного прогона `GET /reviews/{review_run_id}/events`, поэтому поток нужен работающим уже сейчас. NOTIFY из транзакции записи — отдельный шаг; до него источник — опрос базы, как у потока одного прогона. Контракт событий от смены источника не меняется.

## What Changes

- `GET /api/v1/events` реализован: `200` с потоком `text/event-stream`, `401` без сессии, `502 SCM_UNAVAILABLE` при сбое провайдера на подключении; `501` в схеме больше нет.
- Источник — опрос базы раз в секунду одним запросом на соединение: прогоны новее курсора `(updated_at, id)` и окно перепроверки под курсором, чтобы не терять запись, закоммиченную позже соседней с меньшим `updated_at`. Истории поток не отдаёт.
- Тип события: `run.finished` у итогового статуса, `run.created` у прогона, созданного после подключения, при первом его событии, иначе `run.status_changed` — в том числе при изменении без смены статуса.
- Видимость `run.*`: набор зарегистрированных репозиториев из ответа того же расчёта, что `GET /repositories/available`, — один раз при подключении.
- Сессия перепроверяется раз в 5 секунд, `ping` — в тишине не реже раза в 15 секунд, первая строка — `retry: 3000`.
- Опрос не пишет спанов SQL; уход клиента закрывает ответ штатно, без ERROR на спане запроса.
- Индекс `review_runs (updated_at, id)` — миграция `0008`.
- Поток одного прогона не меняется.

## Impact

- Код: `app/api/events.py`, `app/api/repositories.py` (зависимость `available_repositories`), `app/api/reviews.py` (`review_run_list_item` из записи пачки), `app/api/dependencies.py`, `app/api/errors.py`, `app/application/run_feed_events.py`, `app/domain/run_feed.py`, `app/domain/entities.py` (`ReviewRunListEntry`), порты и адаптеры `ReviewRunRepo.list_changed`, `RepositoryRepo.list_by_provider_ids`, `UntracedUnitOfWork`, `telemetry.untraced`.
- База: миграция `0008_add_review_runs_updated_at_index`.
- Контракт: описание и ответы `GET /api/v1/events` в `docs/openapi/openapi.json`; модели событий не меняются.
