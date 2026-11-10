# Tasks

## 1. Скрипт

- [x] 1.1 `scripts/build_summary.py`: `collect` (размеры образа из docker) и `render` (markdown)
- [x] 1.2 `tests/test_build_summary.py` на фикстурах: база есть и нет, trivy, hadolint, CLI

## 2. CI

- [x] 2.1 Job `image`: hadolint и trivy в json без падения, сбор метрик, summary
- [x] 2.2 База develop через кэш Actions: restore везде, save на push в develop

## 3. Проверка

- [x] 3.1 Прогон скрипта на локально собранном образе
- [x] 3.2 `ruff`, `lint-imports`, `pytest`, `openspec validate --all --strict`
