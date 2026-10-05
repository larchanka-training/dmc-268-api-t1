# Tasks

## 1. Домен и wire-формат

- [x] 1.1 `ReviewJob`: `id` — идентификатор сообщения, `review_run_id` — идентификатор прогона, `installation_id` остаётся (`app/domain/entities.py`; после ребейза на `develop`); тест состава полей (`uv run pytest tests/domain/test_review_job.py`)
- [x] 1.2 `to_wire_message`/`from_wire_message` коннектора из `develop` переносят `installation_id` на верхнем уровне тела (`app/infrastructure/queue/rabbitmq.py`); round-trip и отсутствие лишних полей (`uv run pytest tests/queue/test_wire_format.py`)

## 2. Use case приёма

- [x] 2.1 `handle_webhook_event` генерирует новый `job_id` на каждую постановку и указывает `review_run_id` (`app/application/use_cases/handle_webhook.py`); юнит-тест: у двух постановок разных прогонов разные `job_id` и верный `review_run_id` (`uv run pytest tests/application/test_handle_webhook.py`)

## 3. Документация и контракт

- [x] 3.1 `docs/SYSTEM_DESIGN.md` §4.2: пример тела с `job_id`, `review_run_id` и `installation_id` (`uv run pytest tests/test_docs.py`)
- [x] 3.2 E2E видит сообщение со всеми тремя идентификаторами (`uv run pytest tests/api/test_webhooks_e2e.py`)

## 4. Спека и верификация

- [x] 4.1 Delta `job-queue` добавлена после мержа `develop`; синхронизация обеих дельт и архивация — после ревью; `uv run openspec validate --strict`
- [x] 4.2 Полный прогон: `uv run ruff check .`, `uv run lint-imports`, `uv run mypy .`, `uv run pytest`, `uv run alembic heads` — одна голова
