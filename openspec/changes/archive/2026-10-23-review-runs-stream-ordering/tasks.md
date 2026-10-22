## 1. Контракт

- [x] 1.1 Описания потока, `updated_at` и списка: при равном `updated_at` побеждает пришедшая позже версия, снимок — после `open`, поток не фильтруется, перезапрос страницы при фильтрах, семантика `q`
- [x] 1.2 `ReviewRunsStreamEventType` в `app/api/reviews.py`, подключён в `extra_models`
- [x] 1.3 `UtcDatetimeParam` принимает только ISO 8601; причины `timezone_aware` и `datetime_not_iso` в `app/api/errors.py`
- [x] 1.4 Страж времени исключает ровно параметры операций

## 2. Тесты и проверки

- [x] 2.1 Тесты: enum имён событий в контракте, причины для времени без зоны и для unix-времени
- [x] 2.2 Экспорт `docs/openapi/openapi.json`; `ruff check`, `lint-imports`, `mypy`, `pytest`, `export_openapi.py --check`, `openspec validate --all --strict` проходят
