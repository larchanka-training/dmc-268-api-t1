## Why

#364, бэкендовая половина larchanka-training/dmc-268-ui-t1#101. У публичного входа стенда,
nginx интерфейса (`dmc268-ui`), нет метрик: RPS, соединения и доля 4xx и 5xx видны только по
`docker logs`, а в Prometheus есть только API за nginx. Интерфейс теперь отдаёт `stub_status` на
внутреннем порту 8081.

## What Changes

- Receiver `nginx` основного коллектора (`infra/observability/otelcol.yaml`) снимает
  `http://dmc268-ui:8081/stub_status` раз в 30 с, в общий конвейер `metrics`. Коллектор и
  интерфейс уже стоят в сети `dmc268-otlp`, новых сетей и портов нет.
- Дашборд «Обзор dmc-268»: три панели «Вход (nginx)» в конце: запросы в секунду и соединения по
  состоянию из Prometheus, доля ответов по классу кода из логов `dmc268-ui` в Loki (в
  `stub_status` кодов нет).
- В локальном compose интерфейса нет: receiver на каждом скрейпе пишет ошибку в лог коллектора,
  остальные метрики идут как прежде. Отдельного локального конфига не заводим.
- Алерта на 5xx входа нет, только панель.

## Capabilities

### New Capabilities

### Modified Capabilities

- `observability`: новое требование о метриках входа nginx и панелях «Обзора».

## Impact

- `infra/observability/otelcol.yaml`, `infra/observability/grafana/provisioning/dashboards/dmc268-overview.json`,
  `infra/observability.tf` (комментарий), `infra/README.md`, `tests/infrastructure/test_observability_stack.py`.
- API и схема базы не меняются. Конфиг коллектора заливается upload-ом, поэтому обычный деплой
  пересоздаёт `dmc268-otelcol`; базу и брокер он не трогает.
