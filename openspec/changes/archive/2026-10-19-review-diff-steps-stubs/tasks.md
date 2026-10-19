## 1. Заглушки

- [x] 1.1 `GET /api/v1/reviews/{review_run_id}/diff` и `/steps` в `app/api/reviews.py`, ответ `501 NOT_IMPLEMENTED`
- [x] 1.2 Тесты: `501` с сессией, `401` без сессии, `422` на id не UUID

## 2. Контракт и проверки

- [x] 2.1 Экспорт `docs/openapi/openapi.json`
- [x] 2.2 `ruff check`, `lint-imports`, `mypy`, `pytest`, `openspec validate --all --strict` проходят
