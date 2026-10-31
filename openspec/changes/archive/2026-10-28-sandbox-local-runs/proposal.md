# Proposal

## Why

Отладить промпты и код-контекст ревью можно было только на полном стенде: Docker, PostgreSQL,
RabbitMQ, GitHub App и Ollama. Чтобы посмотреть, что модель получит и что воркер опубликует на
конкретном PR, приходилось поднимать всё и открывать настоящий пул-реквест.

Отдельно не видно, какой контекст достаёт tree-sitter: что нашлось в файле, что выбрано вокруг
изменённых строк и что отброшено по бюджету. Сбой грамматики (segfault tree-sitter 0.26.0, #226)
всплывал только в прогоне воркера.

## What Changes

- **Песочница воркера** (`tests/sandbox/`, #229): настоящие `Worker.run` и `run_review` с боевыми
  промптами на фейках — `FakeUnitOfWork`, `FakeChannel`, `FakeGitHub`. Вход — кейс корпуса, свой
  YAML или диапазон git; модель — Ollama, заглушка, оракул по разметке кейса или сценарий из JSON.
  Цели `make sandbox*`, `ollama-up`, `ollama-pull`, README.
- **Песочница tree-sitter** (`tests/sandbox/outline/`, #236): настоящие `fetch_review_diff` и
  `build_review_context` на `FakeGitHub` и `FakeCache`, отчёт — ровно блок `ast` промпта и
  отброшенное. `make sandbox-outline-demo` одной командой проверяет окружение и грамматики
  (`scripts/sandbox-outline-doctor.sh`) и разбирает мок-PR; `make sandbox-outline` — свой кейс,
  диапазон git или файл со строками.
- Общие фейки: `load_llm_script` в `tests/fakes/llm_gateway.py`, `FakeChannel`/`FakeConnection` в
  `tests/fakes/queue.py`, константы репозитория фейкового GitHub — одни для e2e и песочниц.
- `build_llm_gateway` в `app/infrastructure/container.py` становится публичным — песочница
  зовёт этот шов.
- `Makefile`: `make help`, `check` и `test-all` как в CI, тесты с инфраструктурой по компонентам,
  `run-api`/`run-worker`; брокер в тестах — в отдельном vhost `dmc268_test`.

**Не входит:** шаг песочниц в CI — их тесты идут в обычном `uv run pytest`; сбои GitHub и
повторная доставка в песочнице воркера — их покрывают `tests/worker` и `tests/e2e`.

## Capabilities

### New Capabilities

Нет.

### Modified Capabilities

Нет.

`skip_specs: true`: наблюдаемое поведение сервиса не меняется — ни ручки, ни прогон ревью, ни
публикация. Меняются тесты, фейки, скрипты разработчика, `Makefile` и документация.

## Impact

- `tests/sandbox/`, `tests/fakes/`, `tests/eval/rules/corpus.py`, `tests/e2e/`, `tests/worker/`.
- `scripts/sandbox-outline-doctor.sh`.
- `Makefile`, `README.md`, `.env.example`, `.gitignore`.
- `app/infrastructure/container.py`: переименование `build_llm_gateway`, без изменения поведения.
- `make run-api`/`run-worker` без `.env` и явных переменных падают на отсутствующих настройках,
  а не берут значения compose.
- #229 смёржен без OpenSpec-change; этот change описывает его задним числом вместе с #236.
