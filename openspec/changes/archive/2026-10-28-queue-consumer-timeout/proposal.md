## Why

Ревью #230 (переход очереди ревью на quorum, `queue-delivery-limit`) нашло:

- Quorum-очередь, в отличие от классической, применяет `consumer_timeout` брокера (по умолчанию 30 мин). Прогон дольше таймаута брокер забирает у воркера: воркер молча выходит, а при двух потребителях второй получает сообщение живого прогона и переводит прогон в `failed` из-под первого. С `LLM_HOSTED_FALLBACK` худший прогон длится 2460 с.
- Штатная остановка всё же тратила попытку доставки: `nack` с requeue уходил раньше `cancel`, освобождал слот prefetch, брокер выдавал то же или следующее сообщение, и `cancel()` возвращал его `basic.reject`. Тест флакал в ~1–2 % прогонов.
- Спеки `webhook-intake` (`x-max-priority`) и `observability` (спаны `review_jobs`) противоречат коду после перехода.

## What Changes

- Воркер потребляет очередь с `x-consumer-timeout` 2 ч аргументом потребителя. Воркер на старте проверяет, что худший прогон короче, а отмену потребителя брокером пишет в лог.
- Остановка и последнее сообщение при `limit`: сначала `cancel`, потом `nack`/`ack`. Цикл выгрузки буфера удалён.
- Спеки `webhook-intake` и `observability` приведены к quorum-очереди `review_jobs.qq`.

## Capabilities

### New Capabilities

### Modified Capabilities

- `job-queue`: ADDED «Таймаут подтверждения длиннее худшего прогона»; MODIFIED «Штатная остановка не тратит попытку доставки».
- `webhook-intake`: REMOVED «Приоритет задачи передаётся свойством AMQP» (сценарий `x-max-priority` MODIFIED не удалить), ADDED «Приоритет задачи — свойство AMQP-сообщения».
- `observability`: MODIFIED «Спаны очереди — по semantic conventions messaging», «Описание статуса спана не повторяет текст исключения».

## Impact

- Код: `app/infrastructure/queue/rabbitmq.py`, `app/worker/factory.py`.
- Брокер: таймаут — аргумент потребителя, объявление очереди от него не зависит. Брокер, где `review_jobs.qq` объявила промежуточная версия этой ветки с `x-consumer-timeout` аргументом очереди, ответит `406 PRECONDITION_FAILED`: такую очередь нужно удалить (`rabbitmqctl delete_queue review_jobs.qq`). На стенд промежуточная версия не попадала.
- Комментарии в `infra/locals.tf` и `docker-compose.yml` про classic-очередь исправлены.
