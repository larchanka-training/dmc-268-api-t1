Каждая задача с кодом - красно-зелёный виток (скилл `backend-tdd`): сначала тест в названном
файле падает, потом код, потом тест зелёный. Решения - `design.md` (D1-D5), поведение - дельта
`specs/context-assembly/spec.md`.

## 1. Спека

- [x] 1.1 Change `context-surrounding`: `proposal.md`, `design.md`, дельта `context-assembly`. Проверка: `npx @fission-ai/openspec@1.15.0 validate --all --strict`

## 2. Расширенные виды hunk'ов (домен)

- [x] 2.1 Чистая функция `expand_hunks` и константы `SURROUNDING_LINES`, `WHOLE_FILE_MAX_BYTES` в `app/domain/hunk_context.py` (design D1, D3). Тест: `tests/domain/test_hunk_context.py` - совпадение с `git diff -U30` и с файлом целиком, склейка окон, граница начала и конца файла, hunk чистого удаления, `\ No newline at end of file`, несовпадение строк

## 3. Сборка пакета

- [x] 3.1 Аргумент `sources` и второй проход бюджета в `assemble_context` (`app/application/context_assembly.py`, design D2): файлы целиком без AST, AST, окна; `tiers` с `whole_file`/`surrounding`. Тест: `tests/application/test_context_assembly.py` - окно в пакете, маленький файл целиком без AST, окно не вытесняет hunk'и другого файла, файл с не вошедшим hunk'ом не расширяется, `token_count` в пределах лимита
- [x] 3.2 Довыборка текста всех изменённых текстовых файлов в `build_review_context`, AST по скачанным текстам (`app/application/use_cases/build_review_context.py`, design D4). Тест: `tests/application/test_build_review_context.py` - файл без AST-языка довыбирается и получает окно, парсер не вызывается

## 4. Промпт и документация

- [x] 4.0 Песочница контура: статус `whole_file` в `tests/sandbox/outline/runner.py` и печать в `__main__.py`, README. Проверка: `uv run pytest tests/sandbox/outline`

- [x] 4.1 Строка о расширенном контексте в `prompts/review/system.md`. Проверка: `uv run pytest tests/domain/test_prompt.py`
- [x] 4.2 `docs/SYSTEM_DESIGN.md` §5: уровни `surrounding` и `whole_file` описаны как построенные. Проверка: `uv run pytest tests/test_docs.py`

## 5. Синхронизация и проверки

- [x] 5.1 `uv run ruff check .`, `uv run lint-imports`, `uv run mypy`, `uv run python scripts/export_openapi.py --check`, `uv run pytest`
- [x] 5.2 Архив: `openspec archive context-surrounding` синхронизирует `openspec/specs/`. Проверка: `npx @fission-ai/openspec@1.15.0 validate --all --strict`

## 6. Замечания ревью

- [x] 6.1 `expand_hunks` при несовпадении строк возвращает `None`; вид, равный исходному, для `whole_file` - успех, `surrounding` - только при отличии (design D1). Тест: `tests/application/test_context_assembly.py` - новый файл до 8 КБ без AST и с `whole_file`; `tests/domain/test_hunk_context.py` - `None` и вид нового файла
- [x] 6.2 Секреты в контекстных строках вида - метка, строки `+`/`-` как есть (design D6, `app/domain/hunk_context.py`). Тест: `tests/domain/test_hunk_context.py` (токен, ключ PEM с сохранением числа строк), `tests/application/test_context_assembly.py` (токен в окне пакета)
- [x] 6.3 Тест защиты от раздувания (#328) в `_expand`: окно из управляющих символов не помещается, пик памяти ограничен
- [x] 6.4 Спека: условие бюджета улучшения, сценарий «Пакет содержит дифф, AST и правила», сценарий о секрете в окружении; описания лимитов довыборки в `app/config.py`, `docs/SYSTEM_DESIGN.md`, `docs/BACKEND_ARCHITECTURE.md`
- [x] 6.5 Метка на каждую строку многострочного секрета в `redact_secrets` и `_redact_key_tails` (`app/domain/secret_redaction.py`, design D6), без добивки в `_redact_context`. Тест: `tests/domain/test_secret_redaction.py` (ключи, хвост, `authorization` через перевод строки), `tests/domain/test_hunk_context.py` (ключ в окне построчно против `git diff -U30`), `tests/application/test_context_assembly.py` (новый файл больше 8 КБ без `surrounding`, несовпавший текст не меняет пакет)
