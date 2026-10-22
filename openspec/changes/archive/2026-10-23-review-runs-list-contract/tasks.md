## 1. Заглушки

- [x] 1.1 `GET /api/v1/reviews` в `app/api/reviews.py`: модели `ReviewRunListItem`, `ReviewRunListResponse`, фильтры и пагинация, ответ `501 NOT_IMPLEMENTED`; проверка диапазона дат — `_reject_reversed_range`
- [x] 1.2 `GET /api/v1/reviews/events` в `app/api/reviews.py`: модели `ReviewRunStatusChangedEvent`, `ReviewRunFinishedEvent` в `extra_models`, ответ `501 NOT_IMPLEMENTED`
- [x] 1.3 Тесты: `501` с сессией, `401` без сессии, `422` на параметры, `/reviews/events` не попадает в `/reviews/{review_run_id}`, параметры и модели — в экспортированном контракте

## 2. Контракт и проверки

- [x] 2.1 Экспорт `docs/openapi/openapi.json` (`uv run python scripts/export_openapi.py`)
- [x] 2.2 `ruff check`, `lint-imports`, `mypy`, `pytest`, `export_openapi.py --check`, `openspec validate --all --strict` проходят
