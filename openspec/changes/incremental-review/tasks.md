# Tasks

> Реализация — после согласования change. Каждая группа — отдельный PR со своими тестами и
> миграцией. Зависит от #225 и #230.

## 1. Схема данных

- [ ] 1.1 Миграция: таблица `reviewed_hunks` (`merge_request_id`, `path`, `hunk_digest`, `config_digest`, `occurrences`, `has_findings`, `review_run_id`, `stale_at`; `UNIQUE (merge_request_id, path, hunk_digest, config_digest)`); проверка — `alembic upgrade head && alembic downgrade -1` на чистой базе, интеграционный тест уникальности и тест «вернувшийся hunk со stale-записью записывается без ошибки»
- [ ] 1.2 Миграция: `review_runs.cancel_reason` (`superseded`, `base_changed`), `superseded_by_head_sha`; проверка — интеграционный тест репозитория прогонов
- [ ] 1.3 `docs/erd.md`: таблица и правила схемы; проверка — диаграмма совпадает с миграциями

## 2. Вытеснение

- [ ] 2.2 `handle_webhook_event`: устаревшая доставка (`event.head_sha != metadata.head_sha`) → `ignored`, если на `event.head_sha` есть прогон, иначе `503`; проверка — тесты запоздавшей доставки A после B и отстающих метаданных
- [ ] 2.3 `_supersede` (#225) пишет `cancel_reason=base_changed`; приём на `synchronize` прогоны не отменяет; проверка — тест: `synchronize` при активном прогоне прежнего head оставляет его статус
- [ ] 2.4 Проверка в `run_review` при взятии задачи, перед каждым вызовом модели и перед `publishing`: статус прогона и head PR по `GET pulls/{n}`; другой head → `cancelled(superseded)` с этим head; проверка — тест «три push во время анализа» в песочнице воркера (#229), тест доставок B и C в обратном порядке, тест: задача прогона прежнего head подтверждается без запроса диффа

## 3. Область ревью

- [ ] 3.1 Чистые `hunk_digest`, `split_segments`, `config_digest` в `app/domain/review_scope.py`; проверка — юнит-тесты: сдвиг строк не меняет дайджест, сегменты одинаковы в двух прогонах
- [ ] 3.2 Чистая `review_scope(pr_hunks, ledger, config_digest) -> Scope` с подсчётом вхождений, устаревшими записями и записями `has_findings` (в области, помечены перепроверяемыми); проверка — юнит-тесты сценариев «откат и возврат», «копия блока», «вернувшаяся уязвимость», «hunk с находкой проверен снова без находок»
- [ ] 3.3 Запись журнала и отметка устаревания в транзакции `completed`: hunk'и с находками — с `has_findings = true`, без hunk'ов из `omitted`, с переактивацией устаревших записей; проверка — интеграционный тест: `failed`, `cancelled` не пополняют журнал, hunk из `omitted` остаётся в области
- [ ] 3.4 Пустая область → `completed` без модели, gate с `settle_merge_gate`; проверка — тест песочницы «push только lock-файла»
- [ ] 3.5 Сводка ревью по охвату; проверка — тест сводки при открытом `critical`
- [ ] 3.6 Флаг `INCREMENTAL_REVIEW_ENABLED` и теневой режим с метрикой пропущенных hunk'ов; проверка — сценарии `review-pipeline` проходят с выключенным флагом

## 4. Документация и контракт

- [ ] 4.1 `cancel_reason`, `superseded_by` в `ReviewJob`, `export_openapi`; проверка — контрактный тест
- [ ] 4.2 `docs/SYSTEM_DESIGN.md`, `docs/pipeline/PIPELINE_SPEC.md`: область по журналу, вытеснение; проверка — документы не противоречат дельтам
