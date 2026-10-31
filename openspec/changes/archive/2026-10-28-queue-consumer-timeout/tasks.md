## 1. Тесты

- [x] 1.1 Порядок `cancel` → `nack`/`ack` при остановке и `limit`, лог отмены брокером (`tests/worker/test_worker_loop.py`)
- [x] 1.2 `check_consumer_timeout` (`tests/worker/test_startup.py`), таймаут аргументом потребителя на живом брокере (`tests/queue/test_rabbitmq_job_queue.py`), остановка посреди обработки на живом брокере (`tests/worker/test_factory.py`)

## 2. Реализация

- [x] 2.1 `x-consumer-timeout` аргументом `consume` в `Worker.run`
- [x] 2.2 `Worker.run`: `_stop_consuming`, отмена до подтверждения последнего, `for … else` с логом
- [x] 2.3 Комментарии `infra/locals.tf`, `docker-compose.yml`
