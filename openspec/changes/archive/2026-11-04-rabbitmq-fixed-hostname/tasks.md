## 1. Фикс

- [x] 1.1 `hostname = "dmc268-rabbitmq"` у `docker_container.rabbitmq` (`infra/rabbitmq.tf`)
- [x] 1.2 Тест `test_broker_node_name_survives_container_replacement`: без hostname падает, с ним проходит
- [x] 1.3 Воспроизведение в podman: без hostname очередь после замены контейнера пропадает, с hostname остаётся
- [x] 1.4 В `infra/README.md`, «Базу и брокер деплой не пересоздаёт», — зачем hostname и однократный переход стенда на постоянное имя

## 2. Выкат на стенд (после мёржа)

- [x] 2.1 Убедиться, что очереди пусты: `docker exec dmc268-rabbitmq rabbitmqctl -q list_queues name messages` — у всех 0. В DLQ есть сообщения — сначала разобрать: прогоны уже `failed` в базе. Задача, пришедшая между этой проверкой и заменой брокера, останется в старом узле; её прогон `sweep_stale_runs` переведёт в `failed`, потеря принимается
- [x] 2.2 `gh workflow run deploy.yml --ref develop -f allow_stateful_replace=true`; в summary замена `docker_container.rabbitmq` с пометкой о флаге
- [x] 2.3 Узел называется `rabbit@dmc268-rabbitmq`: `docker exec dmc268-rabbitmq rabbitmq-diagnostics -q status | grep -m1 'Node name'`, воркер подключён
- [x] 2.4 Удалить каталоги старых узлов: `docker exec dmc268-rabbitmq sh -c 'cd /var/lib/rabbitmq/mnesia && ls'`, затем `rm -rf` всех `rabbit@<id>*`, кроме `rabbit@dmc268-rabbitmq*`. Их данные не нужны: прогоны по их сообщениям в базе уже `failed`

## Примечания к выполнению

2026-11-04. Перед заменой `review_jobs.qq` и `review_jobs.dlq` пусты. Автодеплой вершины сторож отклонил ([41836939003](https://github.com/larchanka-training/dmc-268-api-t1/actions/runs/41836939003)), ручной запуск с флагом ([41837357122](https://github.com/larchanka-training/dmc-268-api-t1/actions/runs/41837357122)) заменил `docker_container.rabbitmq`: `hostname "34bfab75cf65" -> "dmc268-rabbitmq"`. После выката узел `rabbit@dmc268-rabbitmq`, у `review_jobs.qq` один потребитель. Каталоги узлов `rabbit@34bfab75cf65*`, `rabbit@734912f1b29b*` и `rabbit@c1d27fe901f8*` удалены, на томе остался один узел.
