## 1. Проверка брокера до кода (D2)

- [x] 1.1 Интеграционный тест на `rabbitmq:4.4` (`tests/queue`): задача ревью с приоритетом 5, поставленная после задачи с приоритетом 1, выдаётся потребителю первой; результат - в описание пул-реквеста

## 2. Домен - чистые функции

- [x] 2.1 `SimulationStatus` уже есть; `SimulationSourceKind`, сущности `Simulation`, `SimulationJob`, `SimulationResult` (замечания с вердиктами, отброшенные, контекст по файлам) в `app/domain`
- [x] 2.2 `advance_simulation(simulation, status, now)` в `app/domain/simulation.py`: допустимые переходы, итоговый статус не меняется; итог - `complete_simulation` с результатом и `fail_simulation` с кодом, оба стирают вход (D10); запуск - `new_simulation`
- [x] 2.3 `admit_simulation(active, recent_created_at, now, max_per_window, window)` в `app/domain/simulation.py`: `Admitted` или `Limited(retry_after)`
- [x] 2.4 `code_as_new_file(path, content)` в `app/domain/simulation.py`: `ParsedFile` нового файла с одним hunk'ом, тот же, что `parse_diff` даёт для нового файла в диффе git
- [x] 2.5 `screen_findings` отдаёт отклонённых кандидатов с причиной (`app/domain/verification.py`); `run_review` пишет в `rejected_findings` их число
- [x] 2.6 `apply_verdicts` отдаёт вердикт и причину по каждому оставленному замечанию
- [x] 2.7 `file_contexts(body)` - контекст модели по файлам из тела пакета `context-payload/v1` (им же воспользуется инспектор замечания)
- [x] 2.8 Тесты без базы на 2.2-2.7, в том числе граница окна лимита и `Retry-After` не меньше 1

## 3. Хранение

- [x] 3.1 Миграция: enum-типы `simulation_status`, `simulation_source_kind`, таблица `simulations` с CHECK на `file_path`, `source` и `rules` (только у незавершённой), `result`, `error_code`, `tokens_used`, частичный уникальный индекс незавершённой симуляции пользователя, индексы `(user_id, created_at)` и `(expires_at)`; рабочий `downgrade`, голова одна
- [x] 3.2 Порт `SimulationRepo` и адаптер SQLAlchemy: запись, чтение автором, обновление, есть ли незавершённая, запуски с момента, перевод зависших в `failed` со стиранием входа, удаление истёкших; вторая незавершённая - `add` отвечает `False` (`ON CONFLICT DO NOTHING` по частичному индексу), итоговая строка - `update` отвечает `False`; результат - JSONB с версией формата, перевод в адаптере, `result.context` - через `redact_secrets` (D10)
- [x] 3.3 Фейк порта в `tests/fakes`; интеграционные тесты на PostgreSQL: уникальность незавершённой (в том числе двумя транзакциями одновременно), CHECK (в том числе итоговая строка со входом), каскад при удалении пользователя, круговой перевод результата, секрет в контексте результата заменён меткой

## 4. Конвейер

- [x] 4.1 `assemble_context` берёт `ContextSubject` вместо `ReviewJob`; промпт пропускает пустые репозиторий и номер PR; тесты ревью не меняются
- [x] 4.2 `verify_findings` - общая функция вместо закрытой `_verify` в `review_pipeline.py`
- [ ] 4.3 `build_simulation_context` (`app/application/use_cases`): вход как `ParsedFile`, `filter_diff_files`, правила текста пользователя, AST фрагмента или `no_source` у диффа, пустой контекст PR
- [ ] 4.4 `run_simulation` (`app/application/simulation_pipeline.py`): статусы, `request_review` → `screen_findings` → `verify_findings`, результат, сбой - `failed` с кодом и токенами; итоговая или удалённая симуляция - без работы; спаны `simulation.<этап>`, лог с `simulation_id` без кода
- [ ] 4.5 Тесты на фейке модели: тот же вход - те же замечания, что у `run_review`; таймаут модели; верификатор недоступен; отброшенные отсевом и верификатором; повторная доставка

## 5. Очередь, воркер, настройки

- [ ] 5.1 `JobQueue.enqueue(ReviewJob | SimulationJob)`; адаптер RabbitMQ публикует `type` и приоритет, тело симуляции - `job_id` и `simulation_id`; разбор по `type`, без `type` - ревью
- [ ] 5.2 Воркер передаёт `type` обработчику; обработчик-диспетчер ревью и симуляции в `app/worker/factory.py`
- [ ] 5.3 Проход обслуживания: `sweep_stale_simulations` и `purge_expired_simulations` независимыми шагами; проверка на старте: `SIMULATION_STALE_SECONDS` больше худшего прогона симуляции
- [ ] 5.4 Настройки `SIMULATION_MAX_PER_HOUR` (60), `SIMULATION_TTL_SECONDS` (86400, не меньше 3600), `SIMULATION_STALE_SECONDS` (600); `.env.example`
- [ ] 5.5 Тесты: формат сообщения и диспетчер, сообщение без `type`, шаги обслуживания независимы

## 6. API

- [ ] 6.1 `POST /api/v1/simulations`: `422` за входом без ревьюируемых hunk'ов и правилами больше `RULES_MAX_BYTES`, `429` по `admit_simulation` и уникальности, запись, постановка в очередь, `QUEUE_UNAVAILABLE` - `202` с `failed`; `501` снят из схемы
- [ ] 6.2 `GET /api/v1/simulations/{simulation_id}`: только автору, иначе `404 SIMULATION_NOT_FOUND`
- [ ] 6.3 Тесты ручек: запуск, оба вида `429` и `Retry-After`, брокер недоступен, нечего ревьюить, чужая симуляция
- [ ] 6.4 E2E на фейке модели (`tests/e2e`): `POST` → воркер → опрос `GET` до `completed`; ключ во фрагменте модель видит, в строке симуляции нет ни входа, ни ключа

## 7. Документы, контракт, проверки

- [ ] 7.1 `docs/SYSTEM_DESIGN.md` (воркер и виды задач в очереди), `docs/BACKEND_ARCHITECTURE.md` (симуляции), `docs/erd.md` (таблица `simulations`)
- [ ] 7.2 Экспорт `docs/openapi/openapi.json` (`uv run python scripts/export_openapi.py`)
- [ ] 7.3 `ruff check`, `lint-imports`, `mypy`, `PYTEST_FAIL_ON_SKIP=1 pytest` с базой и брокером, `export_openapi.py --check`, `alembic heads`, `openspec validate --all --strict` проходят
