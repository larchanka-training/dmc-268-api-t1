# Proposal

## Why

CI и pre-commit закреплены на openspec 1.14.1, 15 октября вышла 1.14.2. Скиллы `openspec-*` в `.agents/skills/` отстают сильнее: их сгенерировала 1.13.0, и агенты работают по устаревшим инструкциям — например, `apply` отмечает задачу поиском текста, а не по пути и строке из CLI, `archive` считает задачи сам, а не берёт счётчики из `openspec list --json`.

## What Changes

- Пин `@fission-ai/openspec` поднимается с 1.14.1 до 1.14.2 в `ci.yml` (оба шага job `openspec`), `.pre-commit-config.yaml`, `AGENTS.md` и докстринге `scripts/check_openspec_archived.py`.
- Шесть скиллов `.agents/skills/openspec-*` перегенерированы `openspec update` версии 1.14.2. Локальных правок в них не было — меняется только текст апстрима.

**Не входит:** запись о версии в архивном change `split-long-requirements` — архив не правится.

## Capabilities

### New Capabilities

Нет.

### Modified Capabilities

Нет.

`skip_specs: true`: меняется версия инструмента и инструкции агентов, а не поведение сервиса.

## Impact

- `.github/workflows/ci.yml` — общий для команды файл: job `openspec` начнёт проверять спеки на 1.14.2.
- `.pre-commit-config.yaml`, `AGENTS.md`, `scripts/check_openspec_archived.py`.
- `.agents/skills/openspec-*/SKILL.md` — инструкции, по которым агенты ведут change.
