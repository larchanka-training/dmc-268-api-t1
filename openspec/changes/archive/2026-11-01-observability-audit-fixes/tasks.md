## 1. Конфиги стека

- [x] 1.1 `datasources.yaml`: `tracesToLogsV2` — тег `service.name` → лейбл `service_name`, свой запрос с `trace_id`
- [x] 1.2 `dmc268-overview.json`: соединения по `db_client_connection_state`, CPU — доля занятости
- [x] 1.3 `otelcol.yaml`, `otelcol-docker.yaml`: `service.telemetry.metrics` pull на `0.0.0.0:8888`; `prometheus.yml`: джобы `collector-self`, `collector-docker-self`, `loki`, `tempo`
- [x] 1.4 Экспортёры `otlp_http/tempo`, `otlp_http/loki`
- [x] 1.5 Тесты `tests/infrastructure/test_observability_stack.py`, `test_collector_redaction.py`

## 2. Код

- [x] 2.1 `HealthCheckAccessFilter` на `uvicorn.access` (`app/api/log_redaction.py`), тест в `tests/infrastructure/test_logging.py`
- [x] 2.2 `build_maintenance`: `maintenance_step` в `extra` у трёх шагов, тест в `tests/worker/test_maintenance.py`
- [x] 2.3 `infra/README.md`, `docs/BACKEND_ARCHITECTURE.md`

## 3. Логи контейнеров без SDK

- [x] 3.1 `infra/observability/otelcol-logs.yaml`: `fluent_forward` → маскирование → основной коллектор по OTLP; `groupbyattrs/service` основного переносит имя сервиса в ресурс
- [x] 3.2 `dmc268-otelcol-logs` и сеть `dmc268-logs` в `infra/observability.tf` и compose; драйвер `fluentd` у postgres, реплики и rabbitmq (`local.fluentd_log_opts`, в infra — только со стеком; в compose — `compose.logs.yml`)
- [x] 3.3 Тесты: `tests/test_infra_isolation.py`, `tests/infrastructure/test_observability_stack.py`, `test_collector_redaction.py`, `infra/tests/observability.tftest.hcl`
- [x] 3.4 `infra/README.md` («Логи контейнеров»), `docs/BACKEND_ARCHITECTURE.md`

## 4. Стенд

- [x] 4.1 Из трейса API в Grafana открываются его логи (проверен запрос, который строит переход: по `trace_id` трейса из Tempo Loki находит его строку; в интерфейсе Grafana не открывали, эта проверка перенесена в задачу 2.2 change `grafana-memory`, #323)
- [x] 4.2 Цели `collector-self`, `collector-docker-self`, `collector-logs-self`, `loki`, `tempo` в Prometheus `up`; в логе коллектора нет WARN про `otlphttp`
- [x] 4.3 В Loki нет записей `GET /health`
- [x] 4.4 Выкат ручным запуском с `allow_stateful_replace=true`: `dmc268-postgres`, `dmc268-postgres-replica` и `dmc268-rabbitmq` пересозданы на прежних томах, `docker inspect -f '{{.HostConfig.LogConfig.Type}}'` — `fluentd`, `docker logs` у них показывает записи; повторный план базу и брокер не трогает: в первом деплое после выката (run 41157831159) targeted-план postgres и rabbitmq применился с `0 added, 0 changed, 0 destroyed`, полный план заменяет только `api` и `worker`; контейнеры созданы 2026-10-30 17:36 UTC и с тех пор не пересоздавались
- [x] 4.5 В Loki есть `{service_name="dmc268-postgres"}`, `{service_name="dmc268-postgres-replica"}` и `{service_name="dmc268-rabbitmq"}`; в записях соединений rabbitmq адреса — маркер
- [x] 4.6 `ss -ltn` на сервере: порт 24224 слушается только на `127.0.0.1`
- [x] 4.7 nginx интерфейса: драйвер `fluentd` у `dmc268-ui` (PR в `dmc-268-ui-t1`), в Loki есть `{service_name="dmc268-ui"}`, query и IP скрыты; change не архивируется до него
