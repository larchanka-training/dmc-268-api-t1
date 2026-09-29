## Why

Ревью сейчас нечем ставить в фоновую обработку: `JobQueue` — спроектированный,
но не реализованный порт (`docs/BACKEND_ARCHITECTURE.md`), `app/worker`
описан только как процесс на бумаге. Без очереди и воркера ни пайплайн ревью
(webhook → воркер → LLM → БД), ни последующие тикеты не могут появиться.
RabbitMQ уже выбран архитектурой (`SYSTEM_DESIGN.md` §4.2, `infra/main.tf`),
Redis для этой роли сознательно не подходит и по спеке `backend-base-setup`
не должен обрастать кодом раньше своего настоящего вызывающего кода
(cache/idempotency).

## What Changes

- Новый порт `JobQueue` (`enqueue(job: ReviewJob) -> None`) и адаптер
  `RabbitMQJobQueue` на `pika`.
- Новая доменная сущность сообщения очереди `ReviewJob` по JSON-схеме из
  `SYSTEM_DESIGN.md` §4.2; её `job_id` совпадает с id будущего `ReviewRun`.
- Очередь `review_jobs` с `x-max-priority`, dead-letter exchange
  `review_jobs.dlx` и очередь `review_jobs.dlq`: необработанное сообщение не
  крутится вечно, а уходит в DLQ.
- Новый процесс `app/worker` (`python -m app.worker`) — тонкая точка входа,
  общий consume-цикл (ack/nack), без знания о доменном пайплайне (тот
  подключается отдельным изменением).
- `docker-compose.yml` и CI получают RabbitMQ как реальную зависимость (было:
  только Redis и Postgres).
- **BREAKING**: нет — новый функционал, ничего существующего не меняет
  поведение.

## Capabilities

### New Capabilities
- `job-queue`: постановка задачи ревью в очередь, приоритетная очередь,
  dead-letter при необработанном сообщении, воркер как отдельный процесс,
  который эту очередь потребляет.

### Modified Capabilities
- `backend-base-setup`: требование «Containerized local development
  environment» расширяется — `docker compose up` поднимает ещё и RabbitMQ,
  не только Postgres и Redis.

## Impact

- Новый код: `app/domain/entities.py` (или `jobs.py`), `app/application/ports/job_queue.py`,
  `app/infrastructure/queue/rabbitmq.py`, `app/worker/` (новый пакет),
  `app/worker/handling.py`.
- Изменённый код: `app/infrastructure/container.py`, `app/config.py`
  (`rabbitmq_url`), `pyproject.toml` (зависимость `pika`, import-linter
  контракты для `app.worker`), существующие тесты, где `Settings(...)`
  строится напрямую.
- Инфраструктура: `docker-compose.yml` (сервисы `rabbitmq`, `worker`),
  `.github/workflows/ci.yml` (сервис `rabbitmq` в джобе `test`).
- Документация: `docs/BACKEND_ARCHITECTURE.md` (JobQueue → «Реализованы»,
  `app/worker` в «Процессы»), `docs/SYSTEM_DESIGN.md` §4.2 (DLQ-топология).
- Не затрагивает схему БД — миграций нет.
