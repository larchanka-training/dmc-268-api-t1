# Tasks

## 1. Префикс атрибуции

- [x] 1.1 `app/domain/verification.py`: чистая функция снятия префикса по скелету текста в цикле и отклонение замечания с неснятым префиксом (design D1); тесты на двойной префикс, zero-width, латинскую `o`, `**…**`, `> `, тире и на нетронутое упоминание правила внутри текста в `tests/domain/test_verification.py`

## 2. Верификатор

- [x] 2.1 `app/domain/verification.py`: `apply_verdicts` оставляет `security` уровня `high` и `critical` при вердикте `drop` и возвращает отброшенные и оставленные вопреки вердикту замечания с причинами (design D2); тесты в `tests/domain/test_verification.py`
- [x] 2.2 `app/application/review_pipeline.py`: лог отброшенных и оставленных вопреки вердикту по результату `apply_verdicts`; adversarial-кейс «дифф просит верификатор отбросить F1» в `tests/application/test_prompt_injection.py`

## 3. Затраты и прогресс прогона

- [x] 3.1 `app/domain/llm_errors.py`: `LlmError.tokens_used` (design D3); `_verify` в `app/application/review_pipeline.py` учитывает его при сбое верификатора; тест «верификатор ответил не по схеме» в `tests/application/test_review_pipeline.py`
- [x] 3.2 `app/application/review_pipeline.py`: модель и токены пишутся в транзакции анализа, `_mark_failed` берёт токены из `LlmError` (design D4); тесты «ревью ответило не по схеме» и «сбой публикации сохраняет затраты»
- [x] 3.3 `app/application/review_pipeline.py`: отметка `last_progress_at` после ответа на вызов ревью (design D5); тест сверяет отметку в базе в момент вызова верификатора

## 4. Сводка

- [x] 4.1 `app/application/review_pipeline.py`: без сохранённых замечаний сводка модели убирается из результата (design D6); тесты «верификатор отбросил все» и «часть осталась» по опубликованной сводке

## 5. Промпты и документы

- [x] 5.1 `prompts/review/output-format.md`: `threadFollowUps` не заполняется, пока сервис не передаёт треды; `prompts/review/system.md` и `prompts/review/verifier.md`: смысл записи `<U+XXXX>`; `uv run pytest tests/infrastructure/test_prompts.py` зелёный
- [x] 5.2 `docs/BACKEND_ARCHITECTURE.md`, модель угроз: цена экранирования маркеров, маскировка префикса, иммунитет находок безопасности перед верификатором

## 6. Проверка

- [x] 6.1 `uv run ruff check .`, `uv run mypy app`, `uv run lint-imports`, `uv run pytest` с `TEST_DATABASE_URL` и `TEST_RABBITMQ_URL` без пропусков, `uv run alembic heads`, `openspec validate --all --strict`

## Workflow follow-up

- Заархивировать change в этом же PR до мерджа.
