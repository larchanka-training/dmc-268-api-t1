# Proposal

## Why

`develop` не защищён, а пуш в него — это деплой. Job `deploy` в `.github/workflows/deploy.yml` работает без `environment`, а `workflow_dispatch` запускается с любой ветки: на стенд можно выкатить код, не прошедший CI. Actions и service-образы не прибиты к SHA и digest — подменённый тег исполняется с этими секретами. `actions/checkout` оставляет токен в `.git/config`, а значения `${{ … }}` подставляются прямо в скрипты `run`. `zizmor` на `develop` находит 31 проблему уровня high (персона pedantic).

## What Changes

- Job `deploy` работает в environment `stand`; политика веток environment пускает только `develop`. Ручной запуск с другой ветки GitHub отклоняет до первого шага. Логика `gate` не меняется. Секреты остаются на уровне репозитория — решение команды: перенос требует их значений, а их нет.
- Все `uses` прибиты к полному SHA коммита, рядом — комментарий с тегом `# vX.Y.Z`. Service-образы в `ci.yml`, `eval.yml`, `load.yml` — по digest мультиархитектурного индекса, тег остаётся в ссылке.
- У каждого `actions/checkout` — `persist-credentials: false`: ни одному job токен в git после checkout не нужен.
- Значения `${{ … }}` в `run` (`deploy.yml`, проверка образа и summary) передаются через `env:`.
- Новый job `zizmor` в `ci.yml`: `zizmor --offline --persona pedantic --min-severity high .github/`, версия закреплена. `dangerous-triggers` на `workflow_run` подавлен строкой в `deploy.yml` с обоснованием: `gate` пропускает только прогон CI по `push` и выкатывает проверенный `head_sha`.
- В `.agents/rules/git-and-pr.md`: ветка `deps-update-…` обновляет и SHA-пины actions (`pinact run --update`).

## Capabilities

### Modified Capabilities

- `backend-delivery`: новое требование — стенд разворачивается только из `develop`.

## Impact

- `.github/workflows/*.yml`, `.agents/rules/git-and-pr.md`.
- Environment `stand` с политикой веток `develop` заведён заранее, своих секретов в нём нет: деплой берёт секреты репозитория с теми же именами. Любой workflow из любой ветки по-прежнему читает их напрямую — эта часть риска принята.
- Политику `sha_pinning_required` включать после мержа: до него неприбитые `uses` на `develop` уронят все workflow.
