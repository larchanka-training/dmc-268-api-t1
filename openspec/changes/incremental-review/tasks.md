# Tasks

> Реализация — после согласования change. Каждая группа — отдельный PR со своими тестами и
> миграцией. Зависит от #225 и #230.

## 1. Схема данных

- [ ] 1.1 Миграция: таблица `reviewed_hunks` (`merge_request_id`, `path`, `hunk_digest`, `config_digest`, `occurrences`, `review_run_id`, `stale_at`; `UNIQUE (merge_request_id, path, hunk_digest, config_digest)`); проверка — `alembic upgrade head && alembic downgrade -1` на чистой базе, интеграционный тест уникальности
- [ ] 1.2 Миграция: `review_runs.cancel_reason` (`superseded`, `base_changed`), `superseded_by_run_id` (FK, check «не на себя»); проверка — интеграционный тест репозитория прогонов
- [ ] 1.3 `docs/erd.md`: таблица и правила схемы; проверка — диаграмма совпадает с миграциями

## 2. Вытеснение

- [ ] 2.2 `handle_webhook_event`: устаревшая доставка (`event.head_sha != metadata.head_sha`) → `ignored`; проверка — тест запоздавшей доставки A после B
- [ ] 2.3 Вытеснение на `synchronize` рядом с `_supersede` (#225), `cancel_reason` в обоих путях; проверка — тест «три push во время анализа» в песочнице воркера (#229)
- [ ] 2.4 Проверка статуса в `run_review` при взятии задачи и перед каждым вызовом модели; проверка — тест: задача `cancelled` прогона подтверждается без запроса диффа

## 3. Область ревью

- [ ] 3.1 Чистые `hunk_digest`, `split_segments`, `config_digest` в `app/domain/review_scope.py`; проверка — юнит-тесты: сдвиг строк не меняет дайджест, сегменты одинаковы в двух прогонах
- [ ] 3.2 Чистая `review_scope(pr_hunks, ledger, config_digest) -> Scope` с подсчётом вхождений и устаревшими записями; проверка — юнит-тесты сценариев «откат и возврат», «копия блока», «вернувшаяся уязвимость»
- [ ] 3.3 Запись журнала и отметка устаревания в транзакции `completed`, без hunk'ов с находками; проверка — интеграционный тест: `failed`, `cancelled` не пополняют журнал
- [ ] 3.4 Пустая область → `completed` без модели, gate с `settle_merge_gate`; проверка — тест песочницы «push только lock-файла»
- [ ] 3.5 Сводка ревью по охвату; проверка — тест сводки при открытом `critical`
- [ ] 3.6 Флаг `INCREMENTAL_REVIEW_ENABLED` и теневой режим с метрикой пропущенных hunk'ов; проверка — сценарии `review-pipeline` проходят с выключенным флагом

## 4. Сериализация публикации

- [ ] 4.1 `pg_advisory_lock` на отдельном соединении на время публикации, `PUBLISH_LOCK_WAIT_SECONDS` в `check_consumer_timeout`; проверка — интеграционный тест двух прогонов одного PR на публикации и теста падения держателя

## 5. Документация и контракт

- [ ] 5.1 `cancel_reason`, `superseded_by` в `ReviewJob`, `export_openapi`; проверка — контрактный тест
- [ ] 5.2 `docs/SYSTEM_DESIGN.md`, `docs/pipeline/PIPELINE_SPEC.md`: область по журналу, вытеснение; проверка — документы не противоречат дельтам
