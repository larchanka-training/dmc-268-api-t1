## 1. Решение по сообщению

- [x] 1.1 `ReviewNotStarted` в `run_review`: сбой чтения или захвата, прогон остаётся `queued`
- [x] 1.2 `decide(exc) -> ACK | RETRY | DEAD_LETTER` в `app/worker/handling.py`, без типов pika
- [x] 1.3 RETRY — `basic_reject(requeue=True)` в цикле воркера

## 2. Heartbeat

- [x] 2.1 Обработчик в потоке, цикл соединения в главном, ack/reject из главного; `WORKER_HEARTBEAT_SECONDS`
- [x] 2.2 Потеря соединения посреди обработки — обработчик доводится до конца; `BaseException` потока — в главный
- [x] 2.3 Проход очистки в своём потоке, как обработчик
- [x] 2.4 Пауза `RETRY_DELAY_SECONDS` перед повтором циклом соединения; просьба остановиться её прерывает

## 3. Тесты и завершение

- [x] 3.1 На живом RabbitMQ: обработчик, проход очистки и пауза перед повтором дольше двух интервалов heartbeat — соединение живо; не начатый прогон — повтор, затем ack; после лимита доставок — DLQ
- [x] 3.2 Тесты `decide` на каждый исход, цикла воркера на фейках, пайплайна на сбое захвата
- [x] 3.3 MODIFIED `review-pipeline`: «Сбой обработки задачи переводит прогон в failed» с исключением для `ReviewNotStarted`
- [x] 3.4 `make check`, `pytest`, синхронизация спеки и `openspec archive worker-heartbeat-decide`
