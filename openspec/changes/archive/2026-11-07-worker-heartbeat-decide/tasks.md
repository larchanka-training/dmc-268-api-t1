## 1. Решение по сообщению

- [x] 1.1 `ReviewNotStarted` в `run_review`: сбой чтения или захвата, прогон остаётся `queued`
- [x] 1.2 `decide(exc) -> ACK | RETRY | DEAD_LETTER` в `app/worker/handling.py`, без типов pika
- [x] 1.3 RETRY — `basic_reject(requeue=True)` в цикле воркера

## 2. Heartbeat

- [x] 2.1 Обработчик в потоке, цикл соединения в главном, ack/reject из главного; `WORKER_HEARTBEAT_SECONDS`
- [x] 2.2 Потеря соединения посреди обработки — обработчик доводится до конца; `BaseException` потока — в главный

## 3. Тесты и завершение

- [x] 3.1 На живом RabbitMQ: обработчик дольше двух интервалов heartbeat — ack доходит; не начатый прогон — повтор, затем ack
- [x] 3.2 Тесты `decide` на каждый исход, цикла воркера на фейках, пайплайна на сбое захвата
- [x] 3.3 `make check`, `pytest`, синхронизация спеки и `openspec archive worker-heartbeat-decide`
