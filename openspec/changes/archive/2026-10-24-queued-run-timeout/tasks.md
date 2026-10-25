## 1. Порог для `queued`

- [x] 1.1 Чистая `find_stale`: прогон в `queued` выбирается по `queued_limit`, остальные — по `limit`; тесты в `tests/domain/test_staleness.py`
- [x] 1.2 `sweep_stale_runs` и `build_maintenance` передают порог для `queued`; причина отказа называет порог, по которому прогон выметен
- [x] 1.3 Настройка `queued_run_timeout_seconds` (21600 с, `gt=0`) в `app/config.py`, переопределение через `QUEUED_RUN_TIMEOUT_SECONDS`
- [x] 1.4 Проверка при старте воркера `check_sweep_thresholds`: порог для `queued` больше `STALE_RUN_TIMEOUT_SECONDS`, иначе `WorkerConfigError` до подключения к брокеру
- [x] 1.5 `.env.example`, `docs/BACKEND_ARCHITECTURE.md`
