# Tasks

## 1. Окружение

- [x] 1.1 Добавить `environment: stand` job'у `deploy`: ручной запуск с другой ветки отклоняется; job `image` остаётся без environment

## 2. Цепочка поставки

- [x] 2.1 Прибить все `uses` к SHA с комментарием `# vX.Y.Z`
- [x] 2.2 Прибить service-образы PostgreSQL и RabbitMQ к digest
- [x] 2.3 `persist-credentials: false` у каждого `actions/checkout`
- [x] 2.4 Перенести `${{ … }}` из `run` в `env:` в `deploy.yml`
- [x] 2.5 Записать в `.agents/rules/git-and-pr.md` обновление SHA-пинов в ветке `deps-update-…`

## 3. Проверка

- [x] 3.1 Добавить job `zizmor` в `ci.yml`; на `develop` до правок он падает, после — находок high нет
- [x] 3.2 Проверить синтаксис workflow `actionlint`
