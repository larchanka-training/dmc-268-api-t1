## Why

#201, часть #160. Зависший воркер для деплоя и мониторинга выглядит живым: процесс запущен,
`check-worker.sh` видит «30 с без рестартов», а `WorkerNotConsuming` горит, только когда у
`review_jobs.qq` нет потребителя. Подписанный, но стоящий воркер и задача, висящая на одном шаге,
сейчас не видны никому. Память контейнера у потолка видна только после OOM, через
`ContainerRestarting`.

## What Changes

- `WorkerStalled` (critical, `service: worker`): потребитель у `review_jobs.qq` есть, готовые
  задачи лежат 15 минут, подтверждений нет. Подтверждения берутся из
  `rabbitmq_global_messages_acknowledged_total{queue_type="rabbit_quorum_queue"}`: счётчика по
  очереди `rabbitmq_prometheus` не отдаёт, а quorum-очередь на брокере одна.
- `ReviewJobStuck` (critical, `service: worker`): сообщение выдано и не подтверждено 20 минут, и
  подтверждений за это время нет. Раньше выметателя зависших прогонов (30 минут).
- `ContainerMemoryNearLimit` (warning): `container_memory_usage_total_bytes` больше 90 % лимита
  10 минут; `service` из имени контейнера, чтобы предупреждение глушили только критические
  алерты того же сервиса.
- Тесты `promtool` на каждый алерт, строки в таблице `infra/README.md`, строки `ALERT_RULES`
  страницы статуса: `WorkerStalled` даёт `worker` `outage`, `ReviewJobStuck` даёт `degraded`.
  `ContainerMemoryNearLimit` (warning) страницу не трогает: статус строится по критическим алертам.

## Capabilities

### New Capabilities

### Modified Capabilities

- `observability`: алерты зависшего воркера, застрявшей задачи и памяти контейнера у потолка.

## Impact

- `infra/observability/alerts.yml`, `infra/observability/alerts.test.yml`, `infra/README.md`.
- `app/domain/service_status.py`, `tests/domain/test_service_status.py`.
- Перечень статусов страницы в `openspec/changes/add-status-page/specs/service-status/spec.md`:
  спеки `service-status` ещё нет в `openspec/specs/`, поэтому правится открытый change.
- Код воркера, healthcheck контейнера и сбор метрик не меняются; деплой перезагружает правила
  Prometheus.
