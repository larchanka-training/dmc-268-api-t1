# Tasks

## 1. Версия

- [x] 1.1 Заменить 1.14.2 на 1.15.0 во всех закреплениях; проверить `grep -rn "openspec@1.14" .` вне архива — пусто
- [x] 1.2 Обновить сгенерированные скиллы `.agents/skills/openspec-*` через `openspec update`; проверить, что в дифе только `generatedBy` и текст инструкций самих скиллов

## 2. Проверки

- [x] 2.1 `openspec validate --all --strict` на 1.15.0; проверить, что все элементы проходят
- [x] 2.2 `openspec list --json | python3 scripts/check_openspec_archived.py` на 1.15.0; проверить код возврата 0 и прежний формат JSON
