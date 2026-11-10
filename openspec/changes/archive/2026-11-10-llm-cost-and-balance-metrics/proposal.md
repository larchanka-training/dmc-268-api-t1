## Why

Биллинг включён, и каждый прогон пишет стоимость в `review_runs.cost_eur`, но команда узнаёт о
расходе и об остатке на счёте Eurorouter только запросом руками. Бюджет есть лишь в токенах, а
платим мы в евро. Когда остаток падает ниже 0,10 €, Eurorouter отвечает `402`, и прогоны падают.
Об этом мы узнаём уже по `ReviewRunsFailing`. Ошибку провайдера в логе нельзя сопоставить с его
запросом: `x-request-id` ответа нигде не сохраняется (#119).

## What Changes

- Коллектор считает стоимость прогонов с начала месяца (UTC) тем же SQL-запросом, что и токены:
  метрика `review_llm_cost_eur_month`. Бюджет 20 € — правило записи
  `dmc268:llm_cost_budget_eur_month`, алерты `LlmCostBudgetNearLimit` (> 80 %, warning) и
  `LlmCostBudgetExceeded` (≥ 100 %, critical). Алерты токенов остаются как есть.
- Ревизия 0021 даёт роли `dmc268_monitor` `SELECT` на `review_runs.cost_eur`.
- Воркер с Eurorouter (основным или фоллбэком) отдаёт gauge `review_llm_provider_balance_eur`:
  `GET /credits` первым ключом не чаще раза в 5 минут, остаток — `data.total_credits − data.total_usage`.
  Сбой опроса — одно предупреждение в лог и пропуск точки. Алерты `LlmProviderBalanceLow`
  (< 5 €, warning) и `LlmProviderBalanceCritical` (< 1 €, critical).
- Отказ провайдера по HTTP пишется в лог с кодом и `x-request-id` (поле `review.llm.request_id`),
  без текста ответа. Тот же `x-request-id` становится атрибутом спана httpx, у успешных ответов
  тоже.
- В «Обзоре» две новые панели: «Расход модели за месяц, €» с линией бюджета и «Баланс Eurorouter, €».

Из #119 не делаем две вещи. Метрики `gen_ai.*` на каждый HTTP-вызов дублировали бы
`review_llm_call_duration`, а токены и стоимость уже лежат в базе. `trace_id` и другие метаданные
в Eurorouter тоже не отправляем: провайдеру не нужны наши данные сверх запроса, а сопоставление
обеспечивает его собственный `x-request-id`.

## Capabilities

### New Capabilities

Нет.

### Modified Capabilities

- `observability`: метрика расхода в евро и её бюджет, gauge баланса провайдера, `x-request-id`
  в логе и спане, запрет отправлять провайдеру контекст трейса.

## Impact

- `infra/observability/otelcol.yaml`, `alerts.yml`, `alerts.test.yml`, дашборд `dmc268-overview.json`.
- `alembic/versions/0021_grant_monitor_llm_cost.py`: только `GRANT`, таблица не переписывается.
- `app/infrastructure/llm/eurorouter_balance.py` (новый), `openai_compatible.py`, `telemetry.py`,
  `container.py`, `app/worker/factory.py`.
- Документация: `infra/README.md`, `docs/BACKEND_ARCHITECTURE.md`, `docs/testing/TEST_PLAN.md`.
- В открытом change `add-critical-alerts` требование «Расход токенов для бюджета — из базы»
  дополнено колонкой `cost_eur`, иначе при архивации оно противоречило бы ревизии 0021.
- Страница статуса не меняется: как и алерты бюджета токенов, новые алерты в `ALERT_RULES` не
  попадают. Сбой прогонов из-за пустого счёта и так ловит `ReviewRunsFailing`.
- Новых настроек нет.
