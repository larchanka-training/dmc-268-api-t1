# Tasks

## 1. Песочница воркера (#229)

- [x] 1.1 Входы `tests/sandbox/inputs.py`: кейс корпуса, `--case-file`, `--from-git BASE..HEAD` (дифф от merge-base, сабмодули пропускаются)
- [x] 1.2 Прогон `tests/sandbox/worker.py`: `FakeGitHub.open_pull` и `FakeUnitOfWork`, настоящие `Worker.run` и `build_review_handler` на `FakeChannel`
- [x] 1.3 Модель: `ollama`, `stub`, `oracle` (отвергает повтор путей, код 2) и `script` — `load_llm_script` проверяет записи схемами `ReviewOutput`/`VerificationOutput`
- [x] 1.4 Отчёт `tests/sandbox/__main__.py`: статус, шаги, комментарии, ожидаемые находки, ack/nack, `--json`
- [x] 1.5 Общие фейки очереди `tests/fakes/queue.py` и константы репозитория `FakeGitHub` для e2e и песочницы; `build_llm_gateway` публичный
- [x] 1.6 Цели `make sandbox`, `sandbox-stub`, `sandbox-oracle`, `sandbox-script`, `sandbox-git`, `ollama-up`, `ollama-pull`; `check` и `test-all` как в CI, vhost `dmc268_test`
- [x] 1.7 Тесты `tests/sandbox/`, `tests/fakes/test_*.py`, в том числе Python-файл длиннее 256 строк в отдельном процессе; README `tests/sandbox/README.md`

## 2. Песочница tree-sitter (#236)

- [x] 2.1 Входы `tests/sandbox/outline/sources.py`: `--case`, `--case-file`, `--from-git`, `--file` с `--lines`, ключ `lines:` в кейсе
- [x] 2.2 Прогон `tests/sandbox/outline/runner.py`: `run_outline` через настоящие `fetch_review_diff` и `build_review_context` на `FakeGitHub` и `FakeCache`, `ContextLimits` из `app/worker/factory.py`
- [x] 2.3 Отчёт `tests/sandbox/outline/__main__.py`: блок `ast` промпта и отброшенное, `--rules`, пустой контур — предупреждение, коды 0 / 1 / 2
- [x] 2.4 Проверка окружения и грамматик: `scripts/sandbox-outline-doctor.sh`, `tests/sandbox/outline/grammars.py` (tree-sitter не 0.26.0, разбор образца каждой грамматикой)
- [x] 2.5 Цели `make sandbox-outline-demo` и `make sandbox-outline`; `FILE`/`LINES` — только из командной строки make
- [x] 2.6 Тесты `tests/sandbox/outline/test_outline.py`, в том числе большой файл и doctor в дочернем процессе; README `tests/sandbox/outline/README.md`

## 3. Приёмка

- [x] 3.1 `make sandbox-stub` и `make sandbox-oracle` — все кейсы корпуса `completed`; `make sandbox-outline-demo` — код 0
- [x] 3.2 `uv run ruff check .`, `uv run lint-imports`, `uv run pytest`, `openspec validate --all --strict` — зелёные
