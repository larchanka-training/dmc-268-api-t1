# Tasks

Порядок — тест первым (скилл `backend-tdd`): в каждой задаче сначала падающий
тест, потом код.

## 1. Этап сбоя в домене

- [x] 1.1 `app/domain/entities.py`: поле `ReviewRun.failed_stage:
      ReviewRunStatus | None = None`. Проверка: `uv run mypy .` зелёный,
      существующие тесты `tests/domain` проходят без правок.
- [x] 1.2 `app/domain/lifecycle.py`: чистая функция `advance` при переходе в
      `FAILED` выставляет `failed_stage` — прежний статус, если он один из
      `BUILDING_CONTEXT` / `ANALYSING` / `PUBLISHING`, иначе `None`; при любом
      другом переходе поле не трогает. Проверка: новые случаи в
      `tests/domain/test_lifecycle.py` — сбой из каждого из трёх этапов, сбой
      из `QUEUED` (`None`), переход в `CANCELLED` и `COMPLETED` (`None`).
- [x] 1.3 Вызывающие `advance(…, FAILED, …)` не меняются. Проверка: дополнить
      `tests/application/test_review_pipeline.py` (сбой на анализе →
      `failed_stage == ANALYSING`) и `tests/application/test_stale_sweep.py`
      (зависший в `PUBLISHING` → `failed_stage == PUBLISHING`).

## 2. Хранение

- [x] 2.1 Миграция Alembic по скиллу `alembic-migration`: nullable-колонка
      `review_runs.failed_stage` типа существующего enum `review_run_status`
      (без создания типа) и `CHECK ck_review_runs_failed_stage`: поле пусто
      либо `status = 'failed'` и значение — один из трёх этапов; рабочий
      `downgrade`. `down_revision` — текущая голова `develop` на момент
      реализации. Проверка: `uv run alembic heads` — одна голова; с
      `TEST_DATABASE_URL` — `upgrade head` → `downgrade -1` → `upgrade head`.
- [x] 2.2 Адаптер базы: колонка и ограничение в `ReviewRunRow`
      (`app/infrastructure/db/models.py`), поле в `review_run_to_domain` и в
      обратном маппинге (`app/infrastructure/db/mappers.py`,
      `repositories.py`). Проверка: `tests/db/test_drift.py` без расхождений;
      в `tests/db/test_adapters.py` — прогон с `failed_stage` сохраняется и
      читается обратно; в `tests/db/test_constraints.py` — непустой
      `failed_stage` у прогона не в `failed` отклоняется базой.
- [x] 2.3 `docs/erd.md`: колонка и ограничение в описании `review_runs`.
      Проверка: `uv run pytest tests/test_docs.py`.

## 3. Поля в ответе прогона

- [x] 3.1 `app/api/reviews.py`: `ReviewRunResponse` получает
      `last_progress_at` и `failed_stage`. Проверка: в
      `tests/test_reviews_api.py` — идущий прогон отдаёт `last_progress_at` и
      `failed_stage: null`; упавший на `analysing` отдаёт
      `failed_stage: "analysing"`.

## 4. Решение о событии

- [x] 4.1 Новый модуль `app/domain/run_events.py`: типы событий (`status` с
      полями снимка, `ping`) и чистая функция `next_event` — по прогону,
      последнему отправленному статусу и секундам с последнего события
      возвращает событие либо ничего и признак закрытия потока. Проверка:
      `tests/domain/test_run_events.py` — первый вызов даёт снимок; тот же
      статус — ничего; новый статус — `status`; тишина ≥ 14 секунд — `ping`
      (секунда запаса до обещанных 15: тишина меряется шагами цикла);
      терминальный статус — `status` с закрытием; упавший прогон несёт
      `failed_stage` и `failure_reason`.
- [x] 4.2 Там же чистая функция `format_sse` — событие в текст SSE
      (`event:` / `data:` с JSON в `snake_case`, пустая строка в конце).
      Проверка: тест сверяет точную строку для `status` и для `ping`
      (`data: {}`), время — в ISO 8601 с зоной.

## 5. Цикл потока

- [x] 5.1 `app/application/review_run_events.py`: асинхронный генератор
      `review_run_events` — получает аргументами чтение прогона и ожидание,
      интервал опроса 1 секунда; на каждом шаге зовёт `next_event`, завершается
      после терминального статуса или когда прогон перестал читаться.
      Проверка: `tests/application/test_review_run_events.py` с поддельными
      чтением и ожиданием — последовательность статусов
      `queued → analysing → completed` даёт три события и конец; 14 шагов без
      смены статуса дают `ping`; терминальный прогон даёт одно событие; ни
      одного настоящего `sleep` в тесте.

## 6. Эндпоинт

- [x] 6.1 `app/api/dependencies.py`: зависимость `get_run_reader` — функция,
      которая читает прогон по id через **короткий** `UnitOfWork` из
      контейнера (не `get_unit_of_work`), и `get_sleep` — ожидание между
      проверками, которое тесты подменяют мгновенным. Проверка: тест с контейнером-двойником
      — два вызова открывают и закрывают две отдельные единицы работы.
- [x] 6.2 `app/api/review_events.py`: роутер
      `GET /api/v1/reviews/{review_run_id}/events`, `async def`; первое чтение
      и последующие — через `run_in_threadpool`; 404 до начала потока;
      `StreamingResponse` с `text/event-stream`, `Cache-Control: no-cache`,
      `X-Accel-Buffering: no`, первой строкой `retry: 3000`. Подключить в
      `create_app` (`app/api/factory.py`). Проверка: `tests/test_review_events_api.py`
      — 404 для неизвестного id; для терминального прогона статус 200,
      заголовки, тело ровно `retry: 3000` + одно событие `status`; для
      прогона, который двойник чтения доводит до `completed`, — снимок,
      событие смены и закрытие потока.
- [x] 6.3 `uv run lint-imports` зелёный: `app/domain/run_events.py` и
      `app/application/review_run_events.py` не импортируют FastAPI и
      SQLAlchemy.

## 7. Документы

- [x] 7.1 `docs/openapi/openapi.yaml`: путь `/reviews/{reviewId}/events`
      (`text/event-stream`, 404), схема данных события `ReviewRunStatusEvent`
      в том виде, в каком они уходят по сети, и `failedStage` в `ReviewJob`
      (design D6); то же поле — в `docs/openapi/review-job.schema.json`.
      Проверка: `uv run pytest tests/test_docs.py`.
- [x] 7.2 `docs/pipeline/TEAM_ALIGNMENT_CHECKLIST.md`: способ обновления
      статуса — SSE; `docs/SYSTEM_DESIGN.md` — одна строка про поток событий в
      описании `app/api`. Проверка: `uv run pytest tests/test_docs.py`.

## 8. Сквозная проверка

- [x] 8.1 `uv run ruff check . && uv run mypy . && uv run lint-imports &&
      uv run pytest`; с `TEST_DATABASE_URL` — интеграционные без пропусков;
      `uv run alembic heads` — одна голова;
      `openspec validate add-review-run-events --strict`.
- [x] 8.2 Вживую: `docker compose up -d --build`, вставить репозиторий,
      запрос на изменение и прогон в `queued`, открыть
      `curl -N localhost:8000/api/v1/reviews/<id>/events`; менять статус
      прогона в базе и убедиться, что событие приходит в пределах 2 секунд,
      `ping` — через 15 секунд тишины, а после `failed` поток закрывается и
      несёт `failed_stage`. Сделано: события через ~1 с после записи,
      `ping` через 14 с тишины; 40 одновременных потоков — REST отвечает за
      5 мс, соединений с базой 6; после ухода клиентов опрос прекращается.
- [ ] 8.3 После выкладки на стенд: тот же `curl -N` через
      `https://62.169.24.237/api/v1/reviews/<id>/events` — события доходят без
      задержки буфера. Сообщить в `dmc-268-ui-t1#20` значение
      `stale_run_timeout_seconds` и что эндпоинт готов к подмене mock-источника.
