## Why

#338. RabbitMQ называет узел `rabbit@<hostname>` и хранит его данные в
`/var/lib/rabbitmq/mnesia/rabbit@<hostname>`. У `docker_container.rabbitmq` hostname не задан,
Docker берёт id контейнера, поэтому каждый новый контейнер стартует на томе пустым узлом:
очереди, DLQ и их сообщения остаются в каталоге старого узла и брокеру не видны. На стенде в
томе три каталога узлов, так было уже дважды. Защита тома (`prevent_destroy`, сторож деплоя)
при замене контейнера ничего не сохраняет.

Воспроизведено в podman на `rabbitmq:4.4-alpine`: durable-очередь `review_jobs.dlq` после
`stop`, `rm` и запуска нового контейнера на том же томе без hostname пропадает, а на томе
появляется второй узел. С `--hostname dmc268-rabbitmq` очередь остаётся.

## What Changes

- `hostname = "dmc268-rabbitmq"` у `docker_container.rabbitmq`.
- Тест в `tests/test_deploy_report.py`: у брокера задан постоянный hostname.
- В `infra/README.md` — зачем hostname и как стенд один раз переходит на постоянное имя узла.

## Impact

- Смена hostname заменяет контейнер, и обычный деплой это отклонит. Выкат — ручной запуск
  `deploy.yml` с `allow_stateful_replace=true`. Этот выкат сам переедет на новый узел, поэтому
  перед ним очереди должны быть пусты (порядок — в tasks).
- После выката каталоги старых узлов на томе удаляются руками.
