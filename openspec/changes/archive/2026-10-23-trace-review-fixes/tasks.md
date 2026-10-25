## 1. Статус спана

- [x] 1.1 Тесты: описание статуса — только тип у `process review_jobs` (`Exception` и `BaseException`), у `review.sweep` (вылетевшее и пойманное), у `record_current_exception`; событие `exception` на месте
- [x] 1.2 `record_current_exception`, `consuming_span`, `sweep_span`: статус без текста исключения

## 2. Сэмплирование

- [x] 2.1 Тесты: без настройки — `parentbased_always_on`; `traceidratio` и `parentbased_traceidratio` — предупреждение и `parentbased_always_on`; `parentbased_always_off` по-прежнему применяется
- [x] 2.2 `build_tracer_provider`: отказ от сэмплера по доле

## 3. Без изменения поведения

- [x] 3.1 `TraceIdMiddleware`: `trace_id` в состоянии запроса до разбора `traceparent`; тест — сбой разбора даёт `500` с `trace_id`
- [x] 3.2 Тест потока событий сверяет `spec_version` ASGI с установленным uvicorn

## 4. Документация

- [x] 4.1 `.agents/rules/backend.md`, «Наблюдаемость»: статус без текста исключения, сэмплирование
