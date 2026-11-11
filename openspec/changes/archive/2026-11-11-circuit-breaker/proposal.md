## Why

#294 (спринт 4, бэкенд): у обращений к GitHub и к модели есть backoff, ротация ключей и фоллбэк,
но нет circuit breaker'а. При долгом сбое провайдера каждый прогон заново ждёт таймауты, жжёт
попытки и падает в `failed` и DLQ, хотя через полчаса провайдер вернётся.

## What Changes

- Breaker на GitHub и на каждого провайдера модели в воркере: три отказа подряд размыкают на
  минуту, неудачная проба удваивает паузу до 10 минут, ответ провайдера замыкает.
- Отказ — сеть, таймаут, 5xx до конца попыток. Ответ провайдера с ошибкой (4xx, `429` с ротацией
  ключей, исчерпанный лимит GitHub, ответ не по схеме) — не отказ.
- Провайдер модели на паузе фоллбэк пропускает без вызова. GitHub или все провайдеры модели на
  паузе — прогон не захватывается, а откладывается: сообщение возвращается `nack` с requeue
  через время до пробы (не дольше минуты), попытка доставки не тратится.
- Gauge `review_circuit_breaker_state{provider}`, алерт `ProviderCircuitOpen`; `WorkerStalled` и
  `ReviewJobStuck` при не замкнутом breaker'е молчат.

## Capabilities

### Modified Capabilities

- `job-queue` — отложенный прогон.
- `llm-gateway` — breaker провайдера модели.
- `observability` — метрика и алерт breaker'а, глушение алертов зависания.

## Impact

- `app/domain/circuit_breaker.py`, `app/application/circuit_breaker.py`, адаптеры GitHub, Ollama,
  Eurorouter и фоллбэк, `review_pipeline.run_review`, consume-цикл воркера, `telemetry.py`,
  `container.py`.
- `infra/observability/alerts.yml`, `alerts.test.yml`, `infra/README.md`.
- API без breaker'а: отказ вебхука GitHub переотправит сам. Схема базы и контракт API не меняются.
