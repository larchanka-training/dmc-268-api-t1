## 1. Домен и порт

- [x] 1.1 `ReviewJob` — замороженный dataclass в `app/domain/entities.py`
      (id, event_type, action, repository{provider_id, full_name},
      pull_request{number, head_sha, base_sha}); тест на неизменяемость и
      сравнение по значению в `tests/domain/`.
- [x] 1.2 `JobQueue(Protocol)` в новом `app/application/ports/job_queue.py`
      с методом `enqueue(job: ReviewJob) -> None`; не добавлять в
      `app/application/ports/__init__.py::__all__`.

## 2. Адаптер RabbitMQ

- [x] 2.1 `uv add pika`.
- [x] 2.2 `app/infrastructure/queue/rabbitmq.py`: `RabbitMQJobQueue` —
      объявляет топологию (очередь `review_jobs` с `x-max-priority: 9` и
      `x-dead-letter-exchange: review_jobs.dlx`, fanout-exchange
      `review_jobs.dlx`, очередь `review_jobs.dlq`, биндинг), сериализует
      `ReviewJob` в JSON, публикует с фиксированным приоритетом.
- [x] 2.3 `Settings.rabbitmq_url: str` (обязательное поле) в `app/config.py`.
- [x] 2.4 `Container.job_queue() -> JobQueue` в
      `app/infrastructure/container.py`.
- [x] 2.5 Integration-тест `tests/queue/test_rabbitmq_job_queue.py`
      (`pytest.mark.integration`, `requires_broker`): `enqueue` кладёт
      валидный JSON с ожидаемым приоритетом; сообщение, для которого воркер
      вызвал nack, оказывается в `review_jobs.dlq`. Проверено вживую против
      `rabbitmq:3.13-alpine` в Docker — все 3 теста зелёные. Вскрыт нюанс:
      pika ожидает vhost, закодированный как `%2F`, а не буквальный `//` —
      это учтено в URL для `docker-compose.yml`/CI (задачи 5.1–5.2), а не в
      `infra/outputs.tf` (Terraform-стек вне охвата этого change).

## 3. Воркер-процесс

- [x] 3.1 `app/worker/handling.py`: чистая функция
      `should_ack(exc: BaseException | None) -> bool`; unit-тест без
      брокера.
- [x] 3.2 `app/worker/factory.py`: собирает consume-цикл из `Settings` —
      принимает `Callable[[bytes], None]`, ack на успехе через
      `should_ack`, nack без requeue при исключении. Проверено вживую
      (`tests/worker/test_factory.py`, реальный RabbitMQ): нюанс —
      `Worker.run` обязан `channel.cancel()` + закрыть соединение при
      выходе, иначе брокер продолжает считать канал живым консьюмером и
      следующее сообщение может уйти к уже не читаемому генератору.
- [x] 3.3 `app/worker/__main__.py`: тонкая точка входа, `python -m
      app.worker` запускает цикл из `factory.py` без импорта домена/
      инфраструктуры напрямую (только через фабрику). Обработчик —
      временная заглушка до `add-review-pipeline`.
- [x] 3.4 `pyproject.toml`, `[tool.importlinter]`: `app.worker` — третий
      сиблинг рядом с `app.api : app.infrastructure` (не на уровне
      `app.main` — `factory.py` нужен доступ к инфраструктуре, как и
      `api/factory.py`); в контракт "The entrypoint stays thin" добавлен
      именно `app.worker.__main__`, не весь пакет.
- [x] 3.5 `uv run lint-imports` проходит с новым пакетом — все 3 контракта
      зелёные.

## 4. Тестовый guardrail и обновление существующих тестов

- [x] 4.1 `tests/test_container.py`: обобщить
      `test_every_declared_port_has_an_adapter_and_a_caller` на явную карту
      "порт → адаптер → файл", включить туда `JobQueue → RabbitMQJobQueue`
      (`app/infrastructure/queue/rabbitmq.py`), сохранив прежнюю проверку
      для портов хранения.
- [x] 4.2 Обновить все прямые вызовы `Settings(database_url=...)` в
      `tests/` (`test_health.py`, `test_container.py` и другие, где
      встретятся), добавив `rabbitmq_url=...`.

## 5. Инфраструктура и CI

- [x] 5.1 `docker-compose.yml`: сервис `rabbitmq` (образ
      `rabbitmq:3.13-alpine`, healthcheck `rabbitmq-diagnostics -q ping`);
      сервис `worker` (та же сборка, что `api`, `command: ["python", "-m",
      "app.worker"]`); `api` и `worker` получают `RABBITMQ_URL` (vhost
      `%2F` — см. нюанс в задаче 2.5) и `depends_on: rabbitmq: condition:
      service_healthy`. Проверено вживую: `docker compose up -d --build`
      поднимает все 5 контейнеров, `api` мигрирует базу и отвечает на
      `/health`, `worker` не падает; сообщение, опубликованное в очередь
      через `RabbitMQJobQueue` из контейнера `api`, забирается воркером и
      исчезает из `review_jobs` без ошибок и без DLQ.
- [x] 5.2 `.github/workflows/ci.yml`, джоба `test`: добавить сервис
      `rabbitmq` (образ и healthcheck как в `infra/main.tf`), экспортировать
      `TEST_RABBITMQ_URL` (с `%2F`-encoded vhost).
- [x] 5.3 `tests/conftest.py`: фикстура/маркер `requires_broker`,
      зеркалящий `requires_db`, по переменной `TEST_RABBITMQ_URL`.

## 6. Документация (в этом же change)

- [x] 6.1 `docs/BACKEND_ARCHITECTURE.md`: перенести `JobQueue` из
      «Спроектированы, но не реализованы» в «Реализованы»; в разделе
      «Процессы» отметить `app/worker` как реально существующий код.
- [x] 6.2 `docs/SYSTEM_DESIGN.md` §4.2: зафиксировать DLQ-топологию (имена
      exchange/queue, политика nack) рядом с уже описанным форматом
      сообщения; уточнить, что приоритет пока фиксированный (нет тарифов);
      поправить пример `repository.id` на строку — так у нас типизирован
      `provider_id` везде в домене.

## 7. Финальная проверка

- [x] 7.1 `uv run pytest && uv run ruff check . && uv run lint-imports &&
      uv run mypy .` — зелено. `TEST_DATABASE_URL`+`TEST_RABBITMQ_URL` против
      живых Postgres/RabbitMQ: 132 passed, 0 skipped.
- [x] 7.2 `docker compose up -d --build` поднимает все 5 контейнеров
      (`postgres`, `redis`, `rabbitmq`, `api`, `worker`); `api` мигрирует
      базу и отвечает на `/health`; сообщение, опубликованное через
      `RabbitMQJobQueue` из контейнера `api`, забирается `worker` и
      исчезает из `review_jobs` без DLQ — сквозная проверка вживую, не
      только по логам.
