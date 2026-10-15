# Tasks

## 1. Бамп

- [x] 1.1 Поднять пин openspec до 1.14.2 в `ci.yml`, `.pre-commit-config.yaml`, `AGENTS.md` и `scripts/check_openspec_archived.py`; проверить, что вне архива упоминаний 1.14.1 не осталось
- [x] 1.2 Перегенерировать `.agents/skills/openspec-*` через `openspec update` 1.14.2; проверить, что у всех шести `generatedBy: "1.14.2"` и в диффе нет потерянных локальных правок

## 2. Приёмка

- [x] 2.1 Локально `npx @fission-ai/openspec@1.14.2 validate --all --strict` и проверка архива зелёные
- [x] 2.2 Дождаться зелёного job `openspec` в CI на 1.14.2; приложить ссылку на прогон

## Примечания к выполнению

Проверка: [CI 38822741550](https://github.com/larchanka-training/dmc-268-api-t1/actions/runs/38822741550) — все job зелёные, `openspec` на 1.14.2.
