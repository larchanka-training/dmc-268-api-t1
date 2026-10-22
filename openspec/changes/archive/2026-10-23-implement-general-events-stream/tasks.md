## 1. Ядро

- [x] 1.1 `app/domain/run_feed.py`: чистые `open_feed`, `start_feed`, `advance_feed` — курсор `(updated_at, id)`, окно перепроверки, тип события; `ReviewRunListEntry` в `app/domain/entities.py`
- [x] 1.2 `app/application/run_feed_events.py`: цикл опроса с проверкой сессии и `ping`; `visible_repository_ids` из ответа `list_available_repositories`

## 2. Адаптеры и база

- [x] 2.1 Порт и адаптер `ReviewRunRepo.list_changed` — один запрос: две части с `LIMIT`, джойн запроса на изменение и репозитория, `COUNT … GROUP BY` находок пачки; фейк в `tests/fakes`
- [x] 2.2 Порт и адаптер `RepositoryRepo.list_by_provider_ids`; фейк
- [x] 2.3 Миграция `0008`: индекс `ix_review_runs_updated_at_id`
- [x] 2.4 `UntracedUnitOfWork` и `telemetry.untraced`: опрос без спанов SQL

## 3. HTTP

- [x] 3.1 `app/api/repositories.py`: зависимость `available_repositories`, общая с потоком
- [x] 3.2 `app/api/events.py`: реализация потока, `502` до начала, закрытие ответа при уходе клиента; описание без `501`
- [x] 3.3 Экспорт `docs/openapi/openapi.json`

## 4. Тесты

- [x] 4.1 Домен и цикл: типы событий, курсор с равным `updated_at`, запоздавшая запись, история, `ping`, истёкшая сессия
- [x] 4.2 API: `401`, `502`, три типа, невидимый репозиторий, история, курсор, сессия, `ping`, событие сразу и уход клиента без ERROR
- [x] 4.3 Адаптеры на настоящей базе; единица работы без спанов
