## 1. Контракт

- [x] 1.1 Данные событий потока — `ReviewRunListItem`, `x-sse-events` в ответе потока; `ReviewRunStatusChangedEvent` и `ReviewRunFinishedEvent` удалены из `app/api/reviews.py` и `extra_models`
- [x] 1.2 `ReviewRunListQuery` — модель параметров списка с проверкой диапазона дат (`field_validator` поля `created_to`)
- [x] 1.3 `UtcDatetimeParam` в `app/api/schemas.py` для входного времени; страж времени не проверяет параметры запроса
- [x] 1.4 Описания: правило upsert и порядок «поток, потом снимок», только `run.finished` при итоговом статусе, сортировка по `id` по убыванию, `findings_count`, пустая строка у `q` и `author`, пустой список на невидимый `repository_id`

## 2. Тесты и проверки

- [x] 2.1 Тесты: множество параметров в контракте, данные событий — ссылки на `ReviewRunListItem`, `401` без сессии при неверных параметрах, время с зоной `+03:00`, пустой `author`
- [x] 2.2 Экспорт `docs/openapi/openapi.json`; `ruff check`, `lint-imports`, `mypy`, `pytest`, `export_openapi.py --check`, `openspec validate --all --strict` проходят
