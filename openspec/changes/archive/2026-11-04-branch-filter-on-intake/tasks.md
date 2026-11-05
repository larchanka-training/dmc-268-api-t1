## 1. Фильтр при приёме

- [x] 1.1 Тесты: PR вне фильтра (`opened`, `synchronize`, запись PR и лог атрибутами), в фильтре (`*`, `**`, точное имя), увели из фильтра и вернули (блокировка head), вернули после завершённого ревью (не дубль), пауза важнее фильтра; без правки они падают
- [x] 1.2 `branch_selected` в `app/domain/glob.py` и тесты домена
- [x] 1.3 `BRANCH_FILTERED_CHECK`, сверка после паузы, запись PR и отмена активного прогона в `handle_webhook` (design D1–D3)
- [x] 1.4 Описание `branch_filter` (`*`, `**`, регистр, ручной запуск), экспорт `docs/openapi/openapi.json`

## 2. Фильтр как пауза

- [x] 2.1 Тесты: `pause_check` с веткой вне фильтра, ручной прогон перекрывает фильтр, `head_run_in_charge` после упавшего повтора, отменённый прогон и завершённый после смены фильтра, упавший прогон, тред у PR вне фильтра
- [x] 2.2 `pause_check(repository, head_run, target_branch)` и ветка из записи PR во всех вызовах (design D5)
- [x] 2.3 Заодно: описание `base_sha` в `app/api/reviews.py` и `app/api/schemas.py`: `null` только у прогонов до колонки, а не «пока воркер не узнал» (ревью ui#121), переэкспорт OpenAPI

## 3. Завершение

- [x] 3.1 `ruff`, `lint-imports`, `mypy`, `pytest`, `export_openapi --check`, `openspec validate --all --strict`
- [x] 3.2 Синхронизация спек и `openspec archive branch-filter-on-intake`
