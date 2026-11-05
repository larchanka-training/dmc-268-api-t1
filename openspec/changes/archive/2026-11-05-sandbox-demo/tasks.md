# Tasks

## 1. Проверка окружения

- [x] 1.1 Тесты doctor в `tests/sandbox/test_sandbox.py`: скрипт в дочернем процессе: без `uv`, без `ollama` и не из корня репозитория — `✗` с подсказкой и код 1
- [x] 1.2 Скрипт `scripts/sandbox-doctor.sh`: корень репозитория, uv и Python 3.14, git, curl, ollama, ответ Ollama на `OLLAMA_URL`, скачана ли `MODEL`, не меньше 3 ГБ свободного места; ничего не меняет

## 2. Цели make

- [x] 2.1 `make sandbox-doctor` и `make sandbox-demo`: doctor, `uv sync --all-extras`, `make ollama-up ollama-pull`, прогон воркера на `DEMO_CASE` (по умолчанию `tests/sandbox/examples/case.yaml`), коды 0 / 1 / 2
- [x] 2.2 `make ollama-up` пишет PID в `.ollama.pid`; `make sandbox-down` останавливает только Ollama с этим PID; `.ollama.pid` — в `.gitignore`

## 3. Документация

- [x] 3.1 `tests/sandbox/README.md`: быстрый старт с `make sandbox-demo`, `make sandbox-doctor` и `make sandbox-down`, переменная `DEMO_CASE`, подсказка с doctor в «Что пошло не так»
- [x] 3.2 `README.md`: `make sandbox-demo` — отправная точка песочницы воркера

## 4. Приёмка

- [x] 4.1 `make sandbox-demo` — код 0; `make sandbox-down` останавливает Ollama, поднятую `make ollama-up`, и не трогает Ollama, запущенную не через make
- [x] 4.2 `uv run ruff check .`, `uv run lint-imports`, `uv run pytest`, `openspec validate --all --strict` — зелёные
