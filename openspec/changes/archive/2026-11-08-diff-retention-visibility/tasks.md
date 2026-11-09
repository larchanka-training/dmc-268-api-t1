## 1. Хранение и правило

- [x] 1.1 Чистая функция `diff_expired_at(stats: BodyExpiryStats) -> datetime | None` и агрегат `BodyExpiryStats` (число пакетов, число стёртых тел, `max(body_expired_at)`) в `app/domain/payload_retention.py`; юнит-тесты без базы: все тела стёрты, часть стёрта, пакетов нет
- [x] 1.2 `ContextPayloadRepo.body_expiry_stats_for_run(review_run_id) -> BodyExpiryStats`: порт в `app/application/ports/repositories.py`, SQL-адаптер в `app/infrastructure/db/repositories.py` (один агрегатный запрос без чтения тел, без бизнес-правила), тот же агрегат в `FakeContextPayloadRepo` (`tests/fakes/unit_of_work.py`)
- [x] 1.3 Интеграционные тесты адаптера в `tests/db/test_adapters.py`: все тела стёрты, часть стёрта, пакетов нет — проверяется агрегат

## 2. Прогон

- [x] 2.1 `ReviewRunResponse.diff_expired_at: UtcDatetime | None` с `Field(description=…)`; `get_review` берёт агрегат из `uow.context_payloads` и считает поле функцией `diff_expired_at`; описание ручки дополнено; тесты в `tests/test_reviews_api.py`

## 3. Дифф

- [x] 3.1 `ErrorCode.DIFF_EXPIRED`, модель `DiffExpiredProblem` (410) в `app/api/problems.py`, вариант в `AnyProblem`; словарь `ERRORS_410_COMMIT_NOT_FOUND_OR_DIFF_EXPIRED` в `app/api/errors.py`
- [x] 3.2 Use case `load_run_diff` (`app/application/use_cases/run_diff.py`) получает `diff_expired_at`; первая проверка, раньше `base_sha`, кэша и GitHub, бросает `RunDiffExpired`; юнит-тесты use case: стёртый прогон — `RunDiffExpired`, кэш не читался, вызовов установки и `fetch_diff` нет; стёртый прогон без `base_sha` — `RunDiffExpired`, а не `RunDiffUnavailable`
- [x] 3.3 `get_review_diff` считает `diff_expired_at`, передаёт его в `load_run_diff` и переводит `RunDiffExpired` в `ApiError(HTTPStatus.GONE, ErrorCode.DIFF_EXPIRED, …)`; `_DIFF_DESCRIPTION` дополнен; тесты в `tests/test_reviews_api.py`: стёртый прогон — `410` без вызовов `fetch_diff`; стёртый прогон без `base_sha` — `410 DIFF_EXPIRED`, а не `409 DIFF_UNAVAILABLE`; частично стёртый — `200`; прогон без пакетов контекста — `200` с диффом; пустой дифф — `200` с `files: []`

## 4. Завершение

- [x] 4.1 `uv run python scripts/export_openapi.py`, `docs/openapi/openapi.json` в том же коммите
- [x] 4.2 `docs/erd.md`, `docs/BACKEND_ARCHITECTURE.md`: кто читает `body_expired_at`
- [x] 4.3 Полные проверки (`ruff`, `lint-imports`, `pytest` с базой при `PYTEST_FAIL_ON_SKIP=1`, `make cov-llm-schema`, `export_openapi.py --check`, `openspec validate --all --strict`, `alembic heads`), синхронизация спеки и архив change
