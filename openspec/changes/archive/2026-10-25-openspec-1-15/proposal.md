# Proposal

## Why

openspec закреплён на 1.14.2, во фронтовом репозитории — на 1.14.3. Версии в двух репозиториях расходятся, и проверка `validate --strict` может давать разный результат. Последняя версия — 1.15.0.

## What Changes

- Версия 1.15.0 во всех местах, где она закреплена: `ci.yml`, `.pre-commit-config.yaml`, `AGENTS.md`, `scripts/check_openspec_archived.py`, команда проверок в задачах активного change `add-tree-sitter-index`.
- Сгенерированные скиллы `.agents/skills/openspec-*` обновлены командой `openspec update`: версия генератора записана в самих файлах (`generatedBy`).

Что изменилось с 1.14.2: `openspec update --dry-run`, автономная установка без Node.js, незначительные правки `--json` и `openspec new change`; в 1.14.3 — вычищена зависимость `braces`, исправлено зависание `validate --all` в хуках. Новых проверок `validate --strict` нет, формат `list --json` не менялся, флаги `archive` прежние.

## Capabilities

### New Capabilities

Нет.

### Modified Capabilities

Нет.

`skip_specs: true`: меняется только версия инструмента в CI и документации. Поведение сервиса прежнее.

## Impact

- `.github/workflows/ci.yml`, `.pre-commit-config.yaml`, `AGENTS.md`, `scripts/check_openspec_archived.py`, `.agents/skills/openspec-*`, `openspec/changes/add-tree-sitter-index/tasks.md`.
- Проверяется прогоном `validate --all --strict` и `list --json` через `check_openspec_archived.py` на 1.15.0.
