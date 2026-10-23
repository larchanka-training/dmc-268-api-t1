## 1. Код

- [x] 1.1 Удалить `app/api/review_events.py` и его подключение в `app/api/factory.py`, `ReviewRunStatusEvent` из `extra_models`
- [x] 1.2 Удалить `app/application/review_run_events.py` и `get_run_reader`; `POLL_SECONDS` и `SESSION_CHECK_SECONDS` — в `app/application/run_feed_events.py`; `app/domain/run_events.py` — только `PingEvent` и `PING_AFTER_SECONDS`
- [x] 1.3 Описание `GET /api/v1/events`: поток одного прогона уже заменён; экспорт `docs/openapi/openapi.json`

## 2. Тесты

- [x] 2.1 Удалить тесты старого потока: `tests/test_review_events_api.py`, `tests/api/test_sse_format.py`, `tests/application/test_review_run_events.py`, `tests/domain/test_run_events.py`, SSE-тест в `tests/api/test_tracing.py`
- [x] 2.2 Перенести на `GET /api/v1/events`: `X-Trace-ID` потока равен trace id спана, выход в другой вкладке закрывает поток и повторное подключение — `401`, проверка сессии по часам при медленном чтении, значения интервалов
- [x] 2.3 Отдельной проверки удалённого пути нет: путь неизвестен, как любой другой (решено при ревью #117)

## 3. Документы

- [x] 3.1 `docs/SYSTEM_DESIGN.md`, `docs/pipeline/PIPELINE_SPEC.md`, `docs/pipeline/TEAM_ALIGNMENT_CHECKLIST.md` — общий поток вместо потока одного прогона
