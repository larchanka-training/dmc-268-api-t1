## 1. Домен и порт

- [x] 1.1 Период по умолчанию, предел и типы выборки и результата в `app/domain/analytics.py`
- [x] 1.2 Метод порта `ReviewRunRepo.analytics` и его фейк в `tests/fakes/unit_of_work.py`

## 2. Адаптер

- [x] 2.1 `SqlAlchemyReviewRunRepo.analytics`: CTE учтённых прогонов, `GROUP BY` по UTC-дню, категории и важности, `avg` и `percentile_cont` всего и по репозиториям
- [x] 2.2 Тесты на базе `tests/db/test_analytics_adapter.py`: группировка, зона сессии, учёт прогонов, период, пустая видимость без запроса

## 3. Ручка

- [x] 3.1 `GET /api/v1/analytics` в `app/api/analytics.py`: сессия, проверка периода до GitHub, видимость, `404` на невидимый `repository_id`
- [x] 3.2 Тесты `tests/test_analytics_api.py`: видимость, фильтр, период, форма пустого ответа, `401`, `422`, `502`
- [x] 3.3 Экспорт `docs/openapi/openapi.json` (`uv run python scripts/export_openapi.py`)
