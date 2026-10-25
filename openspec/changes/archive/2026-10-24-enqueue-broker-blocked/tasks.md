# Tasks

## 1. Тесты

- [x] 1.1 Живой алярм брокера: три параллельных `enqueue` отказывают `PublisherBlockedError` за ≤ 10 с, после снятия алярма `enqueue` публикует (`tests/queue/test_broker_blocked.py`, `TEST_RABBITMQ_CONTAINER`)
- [x] 1.2 Lock занят другим потоком: `enqueue` отказывает `PublisherBlockedError` и не открывает соединение (`tests/queue/test_channel_liveness.py`)

## 2. Адаптер

- [x] 2.1 `RabbitMQJobQueue`: `blocked_connection_timeout`, `ConnectionBlockedTimeout` без повтора, `acquire(timeout=…)` вместо `with self._lock` (`app/infrastructure/queue/rabbitmq.py`)

## 3. CI и верификация

- [x] 3.1 `TEST_RABBITMQ_CONTAINER: ${{ job.services.rabbitmq.id }}` в job `test` (`.github/workflows/ci.yml`), строка в README
- [x] 3.2 Полный прогон: `uv run ruff check .`, `uv run lint-imports`, `uv run mypy .`, `uv run pytest`, `openspec validate --all --strict`
