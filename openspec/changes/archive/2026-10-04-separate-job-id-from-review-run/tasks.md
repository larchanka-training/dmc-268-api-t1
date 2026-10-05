## 1. Домен

- [x] 1.1 `app/domain/entities.py`: `ReviewJob` получает поле
      `review_run_id: UUID`; докстринг описывает `id` как идентификатор
      сообщения.

## 2. Приём и обработка

- [x] 2.1 `app/application/review_intake.py`, `accept_review_request`:
      `ReviewJob(id=new_id(), review_run_id=run_id, ...)`.
- [x] 2.2 `app/application/review_pipeline.py`, `run_review`: прогон ищется
      и помечается `failed` по `job.review_run_id`.
- [x] 2.3 `app/application/context_assembly.py`, `assemble_context_stub`:
      тело заготовки несёт и `job_id`, и `review_run_id`.

## 3. Адаптер очереди и воркер

- [x] 3.1 `app/infrastructure/queue/rabbitmq.py`: `to_wire_message` /
      `from_wire_message` переводят поле `review_run_id`.
- [x] 3.2 `app/worker/factory.py`, `build_review_handler`: сбой
      `run_review` пишется в лог с `job_id` и `review_run_id` и
      пробрасывается.

## 4. Тесты

- [x] 4.1 `tests/application/test_review_intake.py`: `job.id` отличается от
      id прогона, `job.review_run_id` с ним совпадает.
- [x] 4.2 `tests/queue/test_wire_format.py`: формат несёт `review_run_id`.
- [x] 4.3 `tests/worker`: сбой обработки пишет в лог оба идентификатора.
- [x] 4.4 Остальные тесты, строящие `ReviewJob`, указывают `review_run_id`.

## 5. Документация

- [x] 5.1 `docs/SYSTEM_DESIGN.md` §4.2: `review_run_id` в примере сообщения и
      одна фраза о том, что воркер ищет прогон по нему.
