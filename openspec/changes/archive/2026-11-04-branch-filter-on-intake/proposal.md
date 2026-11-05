## Why

#313. `branch_filter` сохраняется в настройках репозитория (#272) и отдаётся в
`GET /api/v1/repositories/{id}/settings`, но при приёме вебхука не применяется: ревью идёт для всех
веток. Пользователь настраивает фильтр в кабинете, а он ничего не меняет.

## What Changes

- Приём вебхука `pull_request` сверяет целевую ветку из свежих метаданных PR с `branch_filter`.
  PR вне фильтра не ревьюится: прогона и задачи нет, запись PR обновляется, на head ставится
  `success` «Ветка не в фильтре ревью».
- Смена целевой ветки из фильтра наружу отменяет активный прогон на этом коммите; возврат в фильтр
  обрабатывается как обычная смена ветки (#222) с блокировкой head и новым прогоном.
- Ветка вне фильтра становится ещё одним состоянием паузы в `pause_check` (#335, D11): check фильтра ставят
  и вебхук треда, и завершившийся, упавший или отменённый прогон; ручной прогон, отвечающий за head,
  ставит свой gate.
- Сценарий «Фильтр веток не влияет на приём» переименован в «Пауза важнее фильтра веток».
- Описание поля `branch_filter`: как совпадают `*` и `**`, регистр, ручной запуск;
  `docs/openapi/openapi.json` переэкспортирован.

## Capabilities

### New Capabilities

### Modified Capabilities

- `webhook-intake`: новое требование «Фильтр целевых веток», сценарий о фильтре в требовании о выключенном авторевью.
- `review-pipeline`: фильтр веток в требовании «Прогон при паузе ревью не блокирует merge gate».
- `review-rest-api`: сценарий об описании `branch_filter` в требовании «Настройки репозитория».

## Impact

- `app/application/use_cases/handle_webhook.py`, `app/application/merge_gate_publish.py`,
  `app/application/use_cases/start_review_run.py`, `app/application/review_pipeline.py`,
  `app/application/sync_thread_resolution.py`, `app/domain/glob.py`, `app/api/repositories.py`,
  `docs/openapi/openapi.json`, `docs/erd.md`, `docs/pipeline/PIPELINE_SPEC.md`, тесты.
- Схема базы и контракт ответов не меняются.
