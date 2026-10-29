## Why

Дашборд «Обзор dmc-268» (#130) строит панели глубины очереди и возраста старшего сообщения по лейблу `queue="review_jobs"`. С #230 задачи идут в quorum-очередь `review_jobs.qq`, а классическая `review_jobs` остаётся только на время перехода: панели показывали бы пустую очередь.

## What Changes

- Панели очереди и комментарий скрейпа RabbitMQ в Prometheus смотрят на `review_jobs.qq`.
- Панель «Возраст старшего сообщения» заменена на «Потребители и сообщения в работе»: `rabbitmq_queue_head_message_timestamp` quorum-очередь не экспортирует (проверено на `rabbitmq:4.4-alpine`, per-object), а зависший воркер видно по `rabbitmq_queue_consumers` = 0 или по `rabbitmq_queue_messages_unacked`, который держится дольше худшего прогона.

## Capabilities

### Modified Capabilities

- `observability`: MODIFIED «Дашборд стека — из репозитория» — в составе панелей очередь `review_jobs.qq`.

## Impact

- `infra/observability/grafana/provisioning/dashboards/dmc268-overview.json`, `infra/observability/prometheus.yml`, `tests/infrastructure/test_observability_stack.py`.
- Код, схема базы и контракт API не меняются.
