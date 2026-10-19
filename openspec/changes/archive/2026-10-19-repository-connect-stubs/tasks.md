## 1. Заглушки

- [x] 1.1 `GET /api/v1/repositories/available` и `POST /api/v1/repositories` в `app/api/repositories.py`, ответ `501 NOT_IMPLEMENTED`
- [x] 1.2 Тесты: `501` с сессией, `401` без сессии, `403` без `Origin`, `422` на пустой `providerId`

## 2. Контракт и проверки

- [x] 2.1 Экспорт `docs/openapi/openapi.json`
- [x] 2.2 `ruff check`, `lint-imports`, `mypy`, `pytest`, `openspec validate --all --strict` проходят
