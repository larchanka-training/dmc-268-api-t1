## 1. Конфигурация

- [x] 1.1 `Settings.github_webhook_secret: str` (обязательное поле) в
      `app/config.py`.
- [x] 1.2 Обновить прямые вызовы `Settings(database_url=..., rabbitmq_url=...)`
      в тестах, добавив `github_webhook_secret=...`.

## 2. Заготовка контекста (чистая функция)

- [x] 2.1 `app/application/context_assembly.py`:
      `assemble_context_stub(job: ReviewJob, review_run_id: UUID, now:
      datetime, new_id: Callable[[], UUID]) -> ContextPayload` — чистая
      функция, `tiers=("diff",)`, синтетический hunk для одного
      предсказуемого файла/диапазона строк, `content_sha256` от
      детерминированного плейсхолдера. Unit-тест без БД: детерминирована на
      фиксированных аргументах. Синтетический hunk вынесен отдельной
      функцией `stub_hunks()` в том же модуле — нужен `review_pipeline.py`
      отдельно от `ContextPayload`.

## 3. LlmGateway-заглушка

- [x] 3.1 `app/application/ports/llm_gateway.py`: `LlmGateway(Protocol)` с
      `review(context: ContextPayload) -> LlmReviewResult`
      (`model: str`, `tokens_used: int`, `findings: list[Finding]`); не
      добавлять в `ports.__all__` (та же причина, что у `JobQueue`). Находки
      от модели ещё без id/прогона/меток времени — отдельный DTO
      `LlmFinding`, а не `Finding` из домена.
- [x] 3.2 `app/infrastructure/llm/stub.py`: `StubLlmGateway` — фиксированный
      результат (`model="stub-llm"`, `tokens_used=0`, `findings=[]`).
- [x] 3.3 `Container.llm_gateway() -> LlmGateway` в
      `app/infrastructure/container.py`.
- [x] 3.4 `tests/test_container.py`: добавить `LlmGateway → StubLlmGateway`
      (`app/infrastructure/llm/stub.py`) в карту `OTHER_PORTS`.

## 4. Оркестрация прогона

- [x] 4.1 `app/application/review_pipeline.py: run_review(job: ReviewJob,
      uow: UnitOfWork, llm_gateway: LlmGateway, now: datetime, new_id:
      Callable[[], UUID]) -> None` — переходы `queued → building_context →
      analysing → publishing → completed` через `next_status`/
      `ReviewRunRepo.update`; сохраняет `ContextPayload`
      (`assemble_context_stub`) и находки заглушки через
      `findings.add_validated`; любое исключение → `failed` с
      `failure_reason`. Тесты на фейковых `UnitOfWork`/`LlmGateway`, без
      БД/брокера: успешный путь до `completed` с заполненными
      `model`/`tokens_used`/`duration_seconds`; исключение из
      `llm_gateway.review` → `failed` с причиной; находка, заведомо
      анкорящаяся в `stub_hunks()`, реально доезжает через `add_validated`.
      Уточнение по ходу реализации (не меняет сигнатуру и наблюдаемое
      поведение, только транзакционную границу): каждый переход — свой
      отдельный вход в `with uow:` и свой `commit`, а не одна транзакция на
      весь прогон. Так прогресс, записанный до сбоя на более позднем шаге,
      не откатывается вместе с этим шагом — согласуется с тем, что
      `last_progress_at`/`find_stale` в `review-data-model` рассчитаны на
      восстановление зависшего прогона по факту последнего зафиксированного
      продвижения, а не по факту завершения всего прогона целиком. Заведена
      `tests/fakes/unit_of_work.py` (`FakeUnitOfWork` + фейки шести портов
      хранения) — переиспользуется дальше, не только в этом change.

## 5. Вебхук

- [x] 5.1 `app/api/webhooks.py`: `POST /api/v1/webhooks/github`. Читает
      сырое тело (`await request.body()` до парсинга JSON), сверяет
      `X-Hub-Signature-256` через `hmac.compare_digest` +
      `hashlib.sha256(github_webhook_secret)`. Без/с неверной подписью —
      401, тело не парсится, ничего не пишется и не публикуется. Событие,
      которое не `pull_request` (заголовок `X-GitHub-Event`), или тело без
      `pull_request`/`repository` — 202 `{"status": "ignored"}`, не ошибка
      (см. `docs/testing/TEST_PLAN.md` §3.3).
- [x] 5.2 При валидной подписи: upsert `Repository`
      (`find_by_provider`/`add`), upsert `MergeRequest`
      (`find_by_number`/`add`-или-`update` head_sha), попытка создать
      `ReviewRun(status=QUEUED)` с тем же id, что будущий `ReviewJob.id`.
      Дедуп реализован предварительной проверкой `find_active` (не через
      перехват ошибки целостности от партиал-индекса) — этого достаточно
      для последовательной повторной доставки, которую описывает спека;
      настоящая гонка двух одновременных доставок осталась бы за партиал-
      индексом на уровне БД, но в HTTP-слое такое сейчас вернёт 500, а не
      идемпотентный 202 — сознательное упрощение, не покрытое тестом.
      Дублей нет — не создаём второй прогон, не публикуем вторую задачу,
      отвечаем 202. Новый прогон — публикуем `ReviewJob` через
      `job_queue.enqueue`, отвечаем 202. Идентификаторы — через
      `app/domain/ids.py:new_id()`, не напрямую `uuid.uuid7()`.
- [x] 5.3 Подключить роутер в `create_app` (`app/api/factory.py`).
- [x] 5.4 `tests/test_webhooks.py` (без БД, фейковые `UnitOfWork`/
      `JobQueue` через `Depends`-override): верная подпись → 202 +
      `enqueue` вызван один раз; неверная/отсутствующая подпись → 401,
      `enqueue` не вызван, БД не тронута; неизвестное событие — 202,
      `enqueue` не вызван; повтор того же `head_sha` — один прогон, один
      `enqueue`.
- [x] 5.5 `tests/db/test_webhook_idempotency.py` (`integration`,
      `requires_db`): два вебхука с одинаковым `head_sha` через реальный
      `TestClient` и реальную БД → одна строка `review_runs`, `enqueue`
      (фейковый `JobQueue` в этом тесте) вызван один раз. Проверено вживую
      против `postgres:18-alpine`.

## 6. Подключение воркера

- [x] 6.1 `app/worker/factory.py`/`app/worker/__main__.py`: заменить
      временный no-op-обработчик на `run_review`. Реализовано как
      `build_review_handler(deps: ReviewHandlerDeps) -> MessageHandler` в
      `factory.py` — узкий локальный `Protocol` (`unit_of_work()`,
      `llm_gateway()`), а не весь `Container`, плюс `from_wire_message`
      (обратное к `to_wire_message`, там же в `rabbitmq.py`) и
      `app/domain/ids.py:new_id` вместо прямого `uuid.uuid7()`. Новая
      `run_worker(settings)` в `factory.py` — то, что реально вызывает
      `__main__.py`, потому что сам `__main__.py` не может импортировать
      `app.infrastructure.container.build_container` напрямую (контракт
      "The entrypoint stays thin", только `app.worker.__main__`, не
      `app.worker.factory`). `build_worker` для тестов не тронут — берёт
      произвольный обработчик, как раньше.
- [x] 6.2 `tests/worker/test_review_handler.py`: обработчик разбирает
      сообщение и доводит прогон до `completed` — фейковый `Container`
      (только нужные методы) и фейковый `UnitOfWork`, без БД/брокера.

## 7. Документация

- [x] 7.1 `docs/BACKEND_ARCHITECTURE.md`: `LlmGateway` → таблица
      «Реализованы» с пометкой, что текущий адаптер — заглушка
      (`StubLlmGateway`), реальный транспорт к Ollama остаётся отдельной
      задачей; `VcsGateway` в «Спроектированы, но не реализованы» —
      добавлена пометка про `context_assembly.py` как временную заготовку
      на его месте; раздел «Процессы» — обработчик воркера больше не
      заглушка, а `run_review`.

## 8. Финальная проверка

- [x] 8.1 `uv run pytest && uv run ruff check . && uv run lint-imports &&
      uv run mypy .` — зелено. `TEST_DATABASE_URL`+`TEST_RABBITMQ_URL`
      против живых Postgres/RabbitMQ: 149 passed, 0 skipped. По ходу
      обнаружен и закрыт пробел в конфигурации ruff: `Depends(...)` в
      дефолтах параметров (стандартная идиома FastAPI, уже показанная в
      `.agents/templates/backend/endpoint.md`) до этого change ни разу не
      встречалась в коде и оказалась под запретом B008 без исключения —
      добавлен `tool.ruff.lint.flake8-bugbear.extend-immutable-calls =
      ["fastapi.Depends"]` в `pyproject.toml`.
- [x] 8.2 Сквозная проверка вживую: `docker compose up -d --build` поднял
      все 5 контейнеров; подписанный HMAC `curl` на
      `/api/v1/webhooks/github` → `202 {"status": "accepted"}`; в БД —
      `review_runs.status = completed`, `model = stub-llm`, непустой
      `context_payloads` (`tiers = {diff}`); `review_jobs`/`review_jobs.dlq`
      в RabbitMQ пусты — сообщение реально дошло и обработалось, а не
      застряло и не упало в DLQ.
