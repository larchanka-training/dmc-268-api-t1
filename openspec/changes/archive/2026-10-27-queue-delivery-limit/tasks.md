## 1. Quorum-очередь с лимитом доставок

- [x] 1.1 `declare_topology`: `review_jobs.qq` с `x-queue-type: quorum`, `x-delivery-limit` = `DELIVERY_LIMIT` (5), DLX `review_jobs.dlx`; `x-max-priority` убран
- [x] 1.2 Chaos-тест `tests/chaos/test_poison_message_chaos.py`: сообщение, убивающее воркер на каждой доставке, после `DELIVERY_LIMIT` повторов — в DLQ с `x-first-death-reason: delivery_limit`

## 2. Штатная остановка без траты попытки

- [x] 2.1 `Worker.run`: выданное, но не взятое в работу сообщение и буфер pika при штатном выходе — `nack` с requeue
- [x] 2.2 Тесты: фейковый канал (`test_worker_loop.py`) и живой брокер — `x-delivery-count` после остановки 0, после смерти 1 (`test_factory.py`)

## 3. Документация

- [x] 3.1 `docs/SYSTEM_DESIGN.md` §4.2 (в том числе удаление старой очереди при выкате), `docs/BACKEND_ARCHITECTURE.md`, `.agents/rules/backend.md`
- [x] 3.2 `docs/testing/SECURITY_AND_REGRESSION_REPORT.md`: риск #223 закрыт
