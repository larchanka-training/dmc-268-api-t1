## Why

Ревью трассировки (#84, #110) перед включением экспорта в #89 нашло два места, которые проявятся, как только спаны уйдут в Tempo. Задача #121.

1. Описание статуса спана повторяет текст исключения: `record_current_exception` и автоматический статус `start_as_current_span` пишут `"<тип>: <текст>"`. В тексте бывает ответ провайдера модели (`_error_text` в `openai_compatible.py`) — с цитатой промпта, то есть кода из диффа, — и `DETAIL` psycopg со значениями ключей. Список редакции коллектора в #89 называет событие `exception`, но не описание статуса: текст ушёл бы в Tempo мимо редакции.
2. Сэмплирование не записано. `X-Trace-ID` показывается людям, по нему ищут трейс. Head-сэмплер по доле (`traceidratio`) оставил бы часть trace id без трейса в Tempo: пользователь присылает код из ошибки, а трейса нет.

## What Changes

- Описание статуса ошибки у наших спанов (`process review_jobs`, `review.sweep`, пойманные исключения) — только имя типа исключения. Текст остаётся в событии `exception`, его редактирует коллектор (#89). Серверный спан FastAPI описания не ставит и не меняется.
- `OTEL_TRACES_SAMPLER` с долей (`traceidratio`, `parentbased_traceidratio`) не применяется: сервис пишет `parentbased_always_on` с одним предупреждением при старте. Доля — только хвостовым сэмплированием в коллекторе, где решение принимается по готовому трейсу.
- Без изменения поведения: `TraceIdMiddleware` кладёт `trace_id` в состояние запроса до разбора `traceparent`; тест потока событий сверяет `spec_version` ASGI с установленным uvicorn.

## Capabilities

### Modified Capabilities

- `observability`: описание статуса спана без текста исключения; сэмплирование не прячет трейсы по `X-Trace-ID`.

## Impact

- Код: `app/infrastructure/telemetry.py`, `app/infrastructure/queue/rabbitmq.py`, `app/api/trace_id.py`.
- Тесты: `tests/infrastructure/test_telemetry.py`, `tests/worker/test_maintenance.py`, `tests/api/test_tracing.py`, `tests/test_events_api.py`.
- Контракт API не меняется.
- Не здесь — в #89 (нужен экспорт): SSE в HTTP-метриках, сроки остановки с экспортом, счётчик соединений.
