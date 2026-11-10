## 1. Расход в евро

- [x] 1.1 Ревизия `0021`: `GRANT SELECT (cost_eur)` роли `dmc268_monitor`, откат — `REVOKE`
- [x] 1.2 `otelcol.yaml`: колонка `cost_eur` в запросе `sqlquery/llm-tokens`, метрика `review_llm_cost_eur_month`; `tests/infrastructure/test_alerting.py`, `tests/db/test_pg_monitor_role_migration.py`
- [x] 1.3 `alerts.yml`: правило записи `dmc268:llm_cost_budget_eur_month`, алерты `LlmCostBudgetNearLimit` и `LlmCostBudgetExceeded`; тесты «горит / молчит» в `alerts.test.yml`

## 2. Баланс провайдера

- [x] 2.1 Адаптер `EurorouterBalance` (`app/infrastructure/llm/eurorouter_balance.py`): разбор `/credits`, сбой без точки и с одним предупреждением, кэш на 5 минут; `tests/infrastructure/test_eurorouter_balance.py`
- [x] 2.2 `instrument_llm_balance` (`telemetry.py`) и проводка в `run_worker` только при Eurorouter; тест gauge в `tests/infrastructure/test_telemetry.py`
- [x] 2.3 Алерты `LlmProviderBalanceLow` и `LlmProviderBalanceCritical`; тесты в `alerts.test.yml`

## 3. Идентификатор запроса провайдера

- [x] 3.1 `OpenAiCompatibleGateway`: запись лога с `review.llm.request_id` на каждый отказ по HTTP; тесты в `tests/infrastructure/test_openai_compatible_gateway.py`
- [x] 3.2 `response_hook` инструментирования httpx ставит `review.llm.request_id` на спан; тест в `tests/infrastructure/test_telemetry.py`

## 4. Дашборд и документация

- [x] 4.1 Панели «Расход модели за месяц, €» и «Баланс Eurorouter, €» в `dmc268-overview.json`; сигналы в `tests/infrastructure/test_observability_stack.py`
- [x] 4.2 `infra/README.md` (метрики, алерты), `docs/BACKEND_ARCHITECTURE.md`, `docs/testing/TEST_PLAN.md`
- [x] 4.3 `openspec validate --all --strict`, архив change
