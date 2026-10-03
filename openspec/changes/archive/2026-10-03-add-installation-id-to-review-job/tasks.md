# Tasks

## 1. Домен и wire-формат

- [x] 1.1 `ReviewJob.installation_id: int` в `app/domain/entities.py`; юнит-тест на замороженность и состав полей остаётся зелёным (`uv run pytest tests/domain/test_review_job.py`)
- [x] 1.2 `to_wire_message`/`from_wire_message` в `app/infrastructure/queue/rabbitmq.py` переносят `installation_id` на верхнем уровне тела; тесты round-trip и отсутствия лишних полей (`uv run pytest tests/queue/test_wire_format.py`)

## 2. Use case приёма

- [x] 2.1 `handle_webhook_event` передаёт `event.installation_id` в `ReviewJob` (`app/application/use_cases/handle_webhook.py`); юнит-тест постановки сверяет поле с событием (`uv run pytest tests/application/test_handle_webhook.py`)

## 3. Документация и контракт

- [x] 3.1 `docs/SYSTEM_DESIGN.md` §4.2: поле в примере тела и в списке полей; тест документов (`uv run pytest tests/test_docs.py`)
- [x] 3.2 E2E на записанных fixture'ах видит поле в опубликованном сообщении (`uv run pytest tests/api/test_webhooks_e2e.py`)

## 4. Спека и верификация

- [x] 4.1 Синхронизировать delta в `openspec/specs/webhook-intake/spec.md`, архивировать change; `uv run openspec validate --strict`
- [x] 4.2 Полный прогон: `uv run ruff check .`, `uv run lint-imports`, `uv run mypy .`, `uv run pytest`, `uv run alembic heads` — одна голова
