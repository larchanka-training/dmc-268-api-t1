## 1. Конфиги стека

- [x] 1.1 `datasources.yaml`: `tracesToLogsV2` — тег `service.name` → лейбл `service_name`, свой запрос с `trace_id`
- [x] 1.2 `dmc268-overview.json`: соединения по `db_client_connection_state`, CPU — доля занятости
- [x] 1.3 `otelcol.yaml`, `otelcol-docker.yaml`: `service.telemetry.metrics` pull на `0.0.0.0:8888`; `prometheus.yml`: джобы `collector-self`, `collector-docker-self`, `loki`, `tempo`
- [x] 1.4 Экспортёры `otlp_http/tempo`, `otlp_http/loki`
- [x] 1.5 Тесты `tests/infrastructure/test_observability_stack.py`, `test_collector_redaction.py`

## 2. Код

- [x] 2.1 `HealthCheckAccessFilter` на `uvicorn.access`, тест в `tests/infrastructure/test_logging.py`
- [x] 2.2 `build_maintenance`: `maintenance_step` в `extra` у трёх шагов, тест в `tests/worker/test_maintenance.py`
- [x] 2.3 `infra/README.md`, `docs/BACKEND_ARCHITECTURE.md`

## 3. Стенд

- [ ] 3.1 Из трейса API в Grafana открываются его логи
- [ ] 3.2 Цели `collector-self`, `collector-docker-self`, `loki`, `tempo` в Prometheus `up`; в логе коллектора нет WARN про `otlphttp`
- [ ] 3.3 В Loki нет записей `GET /health`
