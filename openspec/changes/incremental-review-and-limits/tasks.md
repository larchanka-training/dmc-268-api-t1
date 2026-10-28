# Tasks

> Реализация начинается только после согласования change командой (см. открытые вопросы в
> `design.md` и разделе 12 `docs/pipeline/INCREMENTAL_REVIEW_AND_LIMITS_SPEC.md`). Каждая
> группа — отдельный PR в `develop` со своими тестами, миграцией и документацией.

## 1. Схема данных (Backend)

- [ ] 1.1 Миграция: `merge_requests.last_reviewed_head_sha`, `last_reviewed_base_sha`, `last_reviewed_run_id`, `head_updated_at`, `publish_lease_run_id`, `publish_lease_until` (nullable); проверка — `uv run alembic upgrade head && uv run alembic downgrade -1` на чистой базе, `uv run alembic heads` — одна ревизия
- [ ] 1.2 Миграция: `review_runs.review_mode`, `since_sha`, `coverage`, `coverage_reason`, `cancel_reason`, `superseded_by_run_id`, `defer_reason` с нативными enum из `app/domain/enums.py`; проверка — интеграционный тест репозитория прогонов пишет и читает поля
- [ ] 1.3 Миграция: `findings.fingerprint`, `fingerprint_loose`, `enclosing_symbol`, `match_level`, `lifecycle_state`, `reappeared_from_finding_id` (FK `ON DELETE RESTRICT`, check «не на себя»); проверка — интеграционный тест ограничения
- [ ] 1.4 Миграция: таблицы `reviewed_hunks` (`UNIQUE (merge_request_id, hunk_digest, config_digest)`) и `review_run_files`; проверка — интеграционный тест уникальности
- [ ] 1.5 Обновить `docs/erd.md` (таблицы и раздел «Правила, которые обеспечивает схема»); проверка — диаграмма и таблица правил совпадают с миграциями

## 2. Область ревью (Domain + Worker)

- [ ] 2.1 Чистая `hunk_digest(path, hunk) -> str` в `app/domain/review_scope.py`; проверка — юнит-тесты: сдвиг номеров строк не меняет дайджест, смена строки и пути — меняет, trailing whitespace игнорируется
- [ ] 2.2 Чистая `config_digest(prompt_version, rules_digest, context_version) -> str`; проверка — юнит-тест детерминизма
- [ ] 2.3 Чистая `review_mode(watermark, head, compare_status) -> ReviewMode`; проверка — юнит-тесты на `full`/`incremental`/`rebased`/`noop`, включая 404 сравнения
- [ ] 2.4 Чистая `review_scope(pr_files, ledger, config_digest) -> Scope` и `plan_scope(scope, budget, limits) -> ScopePlan` с приоритетом классов файлов; проверка — юнит-тесты сценариев 1, 2, 3, 6, 9, 14 из раздела 10 сводного документа
- [ ] 2.5 Адаптер: `VcsGateway.compare_commits(repo, base, head)` → статус сравнения и переименования (`previous_filename`) в `GitHubVcsGateway`; проверка — интеграционный тест адаптера на записанных ответах GitHub, включая 404
- [ ] 2.6 Сценарий `run_review`: режим, область, пустая область → `completed`/`noop` с check run на head; проверка — тест песочницы воркера (#229) «push только lock-файла»
- [ ] 2.7 Правка `_ALLOWED` в `app/domain/lifecycle.py`: `building_context → completed`; проверка — юнит-тест таблицы переходов
- [ ] 2.8 Запись журнала и CAS-сдвиг знака в транзакции `completed` (порт `ReviewedHunkRepo` и метод `MergeRequestRepo.advance_watermark`); проверка — интеграционный тест: `partial`, `failed`, `cancelled` не пополняют журнал и не сдвигают знак
- [ ] 2.9 Разметка `changed_since_review` в пакете (`context-payload/v2`) в `assemble_context` и строка системного промпта; проверка — юнит-тест сборки пакета и обновлённый `prompts/review/README.md`
- [ ] 2.10 Флаг `INCREMENTAL_REVIEW_ENABLED` в `app/config.py` (по умолчанию `false`) — при выключенном поведение «весь PR diff» сохраняется; проверка — тест, что существующие сценарии `review-pipeline` проходят с выключенным флагом

## 3. Конкурентность и вытеснение (Webhook + Queue + Worker)

- [ ] 3.1 Чистая `is_stale_delivery(event_updated_at, head_updated_at) -> bool`; проверка — юнит-тест «строго раньше»
- [ ] 3.2 `handle_webhook_event`: `stale_event`, отметка `superseded_by` у активного прогона прежнего head, `intake_outcome` в теле 202; проверка — интеграционные тесты сценариев 7 и 8 и повторной доставки старого push
- [ ] 3.3 Чистая `superseded(run, merge_request) -> bool` и проверки в трёх точках `run_review` (взятие, перед вызовом модели, перед публикацией) с переходом в `cancelled`; проверка — тест песочницы «три push во время анализа»
- [ ] 3.4 Аренда публикации: методы `MergeRequestRepo.acquire_publish_lease/release_publish_lease` (CAS) и чистая `lease_available(lease, now) -> bool`; повторная постановка задачи при неудаче ожидания; проверка — интеграционный тест двух прогонов на публикации
- [ ] 3.5 Обновить `docs/SYSTEM_DESIGN.md` §3 и §4.2 и `docs/pipeline/PIPELINE_SPEC.md` §2 (вытеснение, `cancelled`); проверка — документы не противоречат дельтам `webhook-intake` и `incremental-review`

## 4. Сопоставление и состояния замечаний (Domain + Publication)

- [ ] 4.1 Чистые `fingerprint(finding, anchor_line_text, enclosing_symbol, file_identity)` и `fingerprint_loose(...)` в `app/domain/finding_identity.py`; проверка — юнит-тесты: сдвиг строки и пробелы не меняют, категория и правило — меняют
- [ ] 4.2 Чистая `match_findings(candidates, previous, host_lines, renames, window) -> list[Match]` с уровнями L0–L2 и детерминированным жадным выбором; проверка — юнит-тесты сценариев 4, 5, переименования и неоднозначности
- [ ] 4.3 Встроить `match_findings` перед `thread_coverage` в `publish_review.py`, сохранить фоллбэк по базе при сбое сверки; проверка — тест «GraphQL недоступен — дубль не публикуется»
- [ ] 4.4 Чистая `next_lifecycle_state(state, facts) -> LifecycleState`; проверка — юнит-тесты всех переходов раздела 6.3 и сценария 15 (инъекция не меняет состояние)
- [ ] 4.5 Вебхук `pull_request_review_thread` и сверка тредов обновляют `lifecycle_state` (`resolved`/`open`/`outdated`); проверка — интеграционный тест вебхука
- [ ] 4.6 Теневой режим: запись fingerprint и уровней совпадения без влияния на публикацию, метрики L1/L2 в `observability`; проверка — метрики видны в локальном стеке наблюдаемости
- [ ] 4.7 Обновить `docs/BACKEND_ARCHITECTURE.md` (модель угроз: прежние находки не идут в промпт; состояния не по модели); проверка — раздел ссылается на спеку `finding-tracking`

## 5. Лимиты и квоты (Webhook + Worker + Config)

- [ ] 5.1 Порт `RateLimiter` и адаптер PostgreSQL (фиксированное окно, `INSERT … ON CONFLICT DO UPDATE … RETURNING`), миграция `rate_limit_counters`; проверка — интеграционный тест конкурентного инкремента
- [ ] 5.2 Чистая `decide_admission(counters, policy, failed_runs, queue_depth) -> Admission`; проверка — юнит-тесты `reject`/`defer`/`admit` по каждому лимиту раздела 7
- [ ] 5.3 Настройки лимитов в `app/config.py` (все выключены по умолчанию) и `.env.example`; проверка — тест конфигурации: невалидные значения не дают стартовать
- [ ] 5.4 Проход отложенных прогонов рядом со `stale_sweep` (`app/application/deferred_sweep.py`); проверка — тест: ставится только последний отложенный прогон PR, остальные `cancelled`
- [ ] 5.5 Бюджет токенов прогона: чистая `fits_budget(spent, estimate, budget) -> bool` перед каждым вызовом модели, верификатор пропускается при нехватке; проверка — тест песочницы сценария 10
- [ ] 5.6 Квота инсталляции: запрос суммы `tokens_used` за сутки (метод репозитория прогонов) и `skip` с check run `neutral`; проверка — интеграционный тест сценария «квота исчерпана»
- [ ] 5.7 Расширение `filter_diff_files`: `.gitattributes` (`linguist-generated`, `linguist-vendored`) с `base_sha` через `CacheStore`, маркер `@generated`; проверка — юнит-тесты фильтра и сценарий 9
- [ ] 5.8 Покрытие по файлам в `review_run_files` и список непроверенных файлов в сводке ревью; проверка — тест сводки при `partial`
- [ ] 5.9 nginx `limit_req` для `/api/v1/webhooks` в репозитории фронтенда (отдельный sub-issue — frontend); проверка — тест конфигурации nginx в CI фронтенда

## 6. API и контракты (Backend + Frontend)

- [ ] 6.1 Поля прогона (`review_mode`, `since_sha`, `coverage`, `coverage_reason`, `cancel_reason`, `superseded_by`, `defer_reason`) в `ReviewJob` и `ReviewRunListItem`; проверка — контрактные тесты API
- [ ] 6.2 Поля находки (`lifecycle_state`, `match_level`, `reappeared_from`) в модели ответа API, отдельной от схемы ответа модели; проверка — контрактный тест, `docs/openapi/finding.schema.json` не изменён
- [ ] 6.3 Заглушка `GET /api/v1/reviews/{review_run_id}/coverage` с полной схемой и `501`; проверка — тест заглушки
- [ ] 6.4 `intake_outcome` в моделях 202 вебхука; проверка — тест «Тело 202 приёма вебхука»
- [ ] 6.5 Коды причин `TOKEN_BUDGET_EXCEEDED`, `QUOTA_EXCEEDED`, `RATE_LIMITED` в `ErrorCode` и `PIPELINE_SPEC.md` §8; проверка — тест перечня кодов
- [ ] 6.6 Регенерировать `docs/openapi/openapi.json` (`uv run python scripts/export_openapi.py`); проверка — CI-сверка схемы зелёная
- [ ] 6.7 Frontend (sub-issue в `dmc-268-ui-t1`): `cancelled` с причиной и ссылкой на новый прогон, бейдж режима, покрытие `partial`, `skip`/`defer`, состояние замечания; проверка — тесты компонентов `RunProgress`/`ReviewSummary`

## 7. Интеграционная проверка

- [ ] 7.1 Сценарии 1–15 раздела 10 сводного документа в песочнице воркера и e2e-наборе; проверка — `PYTEST_FAIL_ON_SKIP=1 uv run pytest` зелёный
- [ ] 7.2 Нагрузочный прогон (`load.yml`) с включёнными лимитами и фиксация согласованных значений в `app/config.py`; проверка — отчёт прогона приложен к PR
- [ ] 7.3 `npx @fission-ai/openspec@1.15.0 validate --all --strict` зелёный

## Workflow follow-up

- Согласовать change с Backend, Queue, LLM, Frontend, DevOps, QA и закрыть открытые вопросы до задачи 1.1.
- Архивировать change (`openspec archive`) в PR, закрывающем последнюю задачу.
