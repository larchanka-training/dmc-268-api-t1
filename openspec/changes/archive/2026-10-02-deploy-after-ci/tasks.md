# Tasks

## 1. Триггер

- [x] 1.1 Заменить `push` на `workflow_run` по workflow `CI` для `develop`; оставить `workflow_dispatch`
- [x] 1.2 Добавить job `gate`: пропускать прогон, если CI не `success` или запущен не пушем; для `workflow_run` пропускать коммит, который уже не вершина `develop`

## 2. Коммит

- [x] 2.1 Брать коммит из `gate` для checkout в обоих job'ах и для тега образа вместо `GITHUB_SHA`

## 3. Проверка

- [x] 3.1 Проверить синтаксис workflow `actionlint`
