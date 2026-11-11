# Tasks

## 1. Входы

- [x] 1.1 Кейс YAML читается один раз: `case_from_raw` в `tests/eval/rules/corpus.py`, `read_case_yaml` и `case_input` в `tests/sandbox/inputs.py`, `load_outline_case` на них
- [x] 1.2 `touched_diff` строит дифф по номерам строк: `+` для затронутых строк, 3 строки контекста, заголовки hunk'ов; база — файл без затронутых строк

## 2. Отчёт из ответа приложения

- [x] 2.1 `runner.py`: отчёт по файлу из `ast`, `omitted` и `coverage` пакета; удалены `_RecordingExtractor`, `_RecordingVcs`, `_unfence`, `_render_outline`, `OutlineResult`
- [x] 2.2 `--full-outline` — один вызов `extract`; упавший разбор — строка отчёта
- [x] 2.3 `__main__.py`: удалены `--window` и `--payload`; урезанный `omitted` назван в отчёте

## 3. Обвязка и документация

- [x] 3.1 Удалены `scripts/sandbox-outline-doctor.sh`, `tests/sandbox/outline/grammars.py` и их тесты; `make sandbox-outline-demo` — два шага, подсказка про код 139 осталась
- [x] 3.2 В мок-PR — файл больше 8 КБ с блоком `ast` в пакете
- [x] 3.3 `tests/sandbox/outline/README.md` переписан под новый отчёт

## 4. Приёмка

- [x] 4.1 `make sandbox-outline-demo` — код 0
- [x] 4.2 `uv run ruff check .`, `uv run lint-imports`, `uv run pytest`, `openspec validate --all --strict` — зелёные; `app/` не менялся (#253)
