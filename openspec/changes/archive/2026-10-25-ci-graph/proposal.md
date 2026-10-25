# Proposal

## Why

Job `syntax` (compileall) дублирует `ruff check .` и гейтит `lint` и `opentofu`, хотя opentofu с Python не связан, а `test` ждёт `lint` — критический путь CI длиннее, чем нужно, и упавший тест прячется за замечанием линтера (#190). setup-uv и `uv sync` с версиями uv и Python скопированы в шесть job'ов пяти workflow. «Пропущенный тест = упавший» держится на grep вывода pytest в четырёх workflow: локально правило не работает, а с `-s` grep ловит и строки из stdout тестов. Комментарий concurrency в `ci.yml` неверен: при `queue: single` ожидающий push-прогон отменяется следующим, и средний из трёх быстрых пушей в develop CI не получает. trivy сканирует образ CI, а на стенд уезжает образ, собранный заново в `deploy.yml`.

## What Changes

- `ci.yml`: job `syntax` удалён, у job'ов нет `needs` — lint, test, openspec, zizmor, image, opentofu идут параллельно.
- Composite action `.github/actions/python-env` (setup-uv с версиями uv и Python и `uv sync --all-extras --frozen`) — в lint и test `ci.yml`, `eval.yml`, `llm-live.yml`, `load.yml`.
- Плагин pytest `tests/strict_skips.py` в `addopts`: при `PYTEST_FAIL_ON_SKIP=1` пропуск теста или модуля даёт код 1 и список пропущенных. В workflow вместо grep — переменная в env шага; локально — та же переменная.
- concurrency CI: у push — `queue: max` без отмены, у pull_request — отмена и `single`; комментарии в `ci.yml` и `deploy.yml` исправлены.
- `deploy.yml`: trivy по опубликованному digest в job'е `image`, до деплоя, с порогами CI.

**Не входит:** actionlint в CI — его 1.7.12 ключа `queue` не знает (rhysd/actionlint#654).

## Capabilities

### New Capabilities

Нет.

### Modified Capabilities

Нет.

`skip_specs: true`: меняются CI, деплой и тестовая обвязка; наблюдаемое поведение сервиса то же.

## Impact

- `.github/workflows/ci.yml`, `deploy.yml`, `eval.yml`, `llm-live.yml`, `load.yml`; новый `.github/actions/python-env/action.yml`.
- `tests/strict_skips.py`, `tests/test_strict_skips.py`, `pyproject.toml` (`addopts`).
- `AGENTS.md`, `.agents/rules/backend.md`, `README.md`, `docs/testing/TEST_PLAN.md`.
- Обязательных проверок в rulesets нет: удаление job'а `syntax` мёрж не блокирует.
