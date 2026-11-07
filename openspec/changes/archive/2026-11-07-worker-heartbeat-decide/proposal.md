## Why

#157. У воркера выключен heartbeat (`parameters.heartbeat = 0`): обработчик шёл в главном потоке минутами, цикл `BlockingConnection` стоял, и с heartbeat брокер рвал бы соединение. Цена — мёртвое соединение брокер не видит, сообщение висит у потребителя до TCP-таймаута. Решение по сообщению — `should_ack(exc) -> bool`: только ack или DLQ, так что временный сбой до начала прогона (база недоступна на чтении или захвате) уходит в DLQ без повтора, а сбой захвата ещё и помечает прогон `failed` — ревью теряется.

## What Changes

- Обработчик — в отдельном потоке, главный крутит цикл соединения (`process_data_events`) и отвечает на heartbeat; ack и reject — из главного потока после обработки (канал pika не потокобезопасен). Heartbeat — `WORKER_HEARTBEAT_SECONDS` (30 с).
- `should_ack` → `decide(exc) -> ACK | RETRY | DEAD_LETTER` без типов pika. RETRY — `ReviewNotStarted`: сбой чтения или захвата прогона, прогон остался `queued`. Повтор — `basic_reject(requeue=True)`: попытку доставки он тратит, и после `DELIVERY_LIMIT` quorum-очередь уводит сообщение в DLQ.
- `run_review`: сбой чтения или захвата — `ReviewNotStarted`, прогон не переводится в `failed`.
- Потеря соединения посреди обработки: обработчик доводится до конца, процесс выходит без ack; повтор подтвердит завершённый прогон. `KeyboardInterrupt` и `SystemExit` из потока обработчика пробрасываются в главный, как раньше.
- Не делаем: переход на AMQP 1.0 (у `python-qpid-proton` нет Linux-колёс).

## Capabilities

### New Capabilities

### Modified Capabilities

- `job-queue`: heartbeat воркера и повтор не начатого прогона.

## Impact

- `app/worker/factory.py`, `app/worker/handling.py`, `app/application/review_pipeline.py`, тесты воркера, пайплайна и очереди (`tests/queue/test_worker_heartbeat.py` — на живом RabbitMQ).
- Схема, API и формат сообщений не меняются. Деплой пересоздаёт контейнер воркера (новый образ), брокер не трогается.
