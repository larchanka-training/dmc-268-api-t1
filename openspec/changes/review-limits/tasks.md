# Tasks

> После `incremental-review`. Каждая группа — отдельный PR. Лимиты по умолчанию выключены.

## 1. Схема данных

- [ ] 1.1 Миграция: `review_runs.coverage`, `coverage_reason`, таблица `review_run_files`, `repositories.installation_id`; проверка — `alembic upgrade head && alembic downgrade -1`, интеграционный тест чтения `NULL` как `complete`
- [ ] 1.2 Приём пишет `installation_id` из payload; проверка — тест первого вебхука после миграции

## 2. Покрытие и gate

- [ ] 2.1 `build_merge_gate_check` учитывает покрытие (`failure` с перечнем); проверка — тесты публикации, пересчёта по треду и `settle_merge_gate` с `partial`
- [ ] 2.2 `_duplicate_of`: неполный последний прогон не дубль; проверка — тест повтора доставки после `partial`

## 3. Лимиты

- [ ] 3.1 Чистые `plan_scope(scope, limits)` с приоритетом CI/инфраструктуры и `fits_budget(spent, estimate, budget)`; проверка — юнит-тесты сценариев спеки
- [ ] 3.2 Бюджет токенов перед каждым вызовом модели, пропуск верификатора; проверка — тест песочницы (#229)
- [ ] 3.3 Квота: сумма `tokens_used` по `installation_id` за 24 ч, прогон `none`/`quota`; проход добора рядом со `stale_sweep`; проверка — интеграционный тест «квота исчерпана → восстановилась»
- [ ] 3.4 `filter_diff_files`: `.gitattributes` с `base_sha` через `CacheStore`; проверка — юнит-тесты, включая `@generated` от автора PR
- [ ] 3.5 Настройки лимитов в `app/config.py` и `.env.example`, выключены по умолчанию; проверка — тест конфигурации

## 4. Контракт и документация

- [ ] 4.1 `coverage`, `coverage_reason` в `ReviewJob`, `export_openapi`; проверка — контрактный тест
- [ ] 4.2 Сводка ревью перечисляет непроверенные файлы; проверка — тест сводки при `partial`
- [ ] 4.3 `docs/pipeline/PIPELINE_SPEC.md` (лимиты и коды причин); проверка — документ не противоречит дельтам
