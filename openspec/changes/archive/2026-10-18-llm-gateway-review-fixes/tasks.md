# Tasks

## 1. Транспорт к модели

- [x] 1.1 `app/infrastructure/llm/structured.py`: `RawReply.truncated`, сбой без повтора на обрезанном ответе, токены в `LlmOutputError` и в `LlmError` из `send` (design D1, D2); тесты в `tests/infrastructure/test_llm_structured.py`
- [x] 1.2 `app/infrastructure/llm/ollama.py` и `openai_compatible.py`: `done_reason` и `finish_reason` в `truncated`, токены в сбое «запрос больше окна», `from None` на сбое транспорта Ollama; тесты адаптеров
- [x] 1.3 `app/infrastructure/llm/fallback.py`: сумма токенов провайдеров в ответе и в сбое (design D2); тесты в `tests/infrastructure/test_fallback_gateway.py`
- [x] 1.4 `app/infrastructure/container.py`: таймаут подключения 10 секунд (design D3); тест в `tests/test_container.py`

## 2. Конфигурация и старт

- [x] 2.1 `app/worker/factory.py`: `check_model_deadline` при старте воркера (design D4); тесты в `tests/worker/test_startup.py`
- [x] 2.2 `app/config.py`: пустой `LLM_BACKEND` - `None`; `docker-compose.yml`, `.env.example`, `infra/variables.tf`, `infra/README.md`, `.github/workflows/deploy.yml` без молчаливого `stub` (design D5); `tofu validate` и тест старта воркера с пустым `LLM_BACKEND`

## 3. Контекст PR

- [x] 3.1 `app/application/context_assembly.py`: обрезка описания по доле и `index` у сообщения коммита (design D6, D7); тесты в `tests/application/test_context_assembly.py`
- [x] 3.2 `app/domain/prompt.py`: подпись «Описание PR (обрезано)» и номер коммита из `index`; тесты в `tests/domain/test_prompt.py`
- [x] 3.3 `app/application/ports/vcs_gateway.py`, `app/infrastructure/vcs/github.py`: на PR длиннее 250 коммитов новые не приходят - в docstring

## 4. Тесты и CI

- [x] 4.1 `pyproject.toml`: `addopts = ["-m", "not live"]`; `ci.yml` без дублирующего `-m`; `uv run pytest` без пропусков при `TEST_DATABASE_URL` и `TEST_RABBITMQ_URL`
- [x] 4.2 `.github/workflows/llm-live.yml`: пути `app/infrastructure/prompts.py`, `app/worker/factory.py`, `app/domain/context_budget.py`, `uv.lock`

## 5. Проверка

- [x] 5.1 `uv run ruff check .`, `uv run mypy .`, `uv run lint-imports`, `uv run pytest` с базой и брокером без пропусков, `uv run alembic heads`, `openspec validate --all --strict`

## Workflow follow-up

- Заархивировать change в этом же PR до мерджа.
