## Why

#150. Лимит памяти контейнера брокера (512 МБ) RabbitMQ не видит: порог памяти он считает как
0.6 от памяти хоста. На стенде `rabbitmq-diagnostics status` показывает «computed to: 5.0004 gb»,
поэтому под нагрузкой OOM убьёт контейнер раньше, чем брокер включит flow control. Порог
свободного диска по умолчанию 50 МБ: диск кончится раньше, чем брокер заблокирует издателей.
Healthcheck `ping` зеленеет ещё до того, как поднят листенер AMQP.

## What Changes

- `infra/rabbitmq/30-dmc268.conf`: `vm_memory_high_watermark.absolute = 300MiB`,
  `disk_free_limit.absolute = 1GB`. На стенде файл кладётся `upload` в `conf.d`, в compose
  монтируется.
- Healthcheck брокера на стенде и в compose — `check_port_connectivity`.
- Комментарий в `infra/rabbitmq.tf` про порог «от лимита cgroup» исправлен, в `infra/README.md`
  добавлено, почему пороги абсолютные.
- Тест: порог памяти в конфиге ниже потолка контейнера.

Из постановки уже сделано раньше: `stop_timeout` и `destroy_grace_seconds`. `enabled_plugins` не
нужен: образ сам включает `rabbitmq_prometheus`, а конфиг этот файл не трогает. Издатель
переживает блокировку брокера с #143.

## Capabilities

### New Capabilities

### Modified Capabilities

- `backend-delivery`: новое требование «Брокер работает в пределах лимитов контейнера».

## Impact

- `infra/rabbitmq.tf`, `infra/rabbitmq/30-dmc268.conf`, `docker-compose.yml`, `infra/README.md`, тест.
- `upload` и healthcheck заменяют контейнер брокера, и обычный деплой это отклонит. Выкат вместе с
  PR #339 (#338) одним ручным запуском `allow_stateful_replace=true`, по tasks change `rabbitmq-fixed-hostname`.
