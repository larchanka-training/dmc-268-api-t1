## 1. Вынос копии

- [x] 1.1 `.github/scripts/db-backup/pull.sh` — адаптер: свежая копия со стенда по SSH, шифрование трубой, отказ без ключа и без копии моложе 26 ч
- [x] 1.2 `.github/scripts/db-backup/verify.sh` — адаптер: расшифровка трубой в `pg_restore` чистой базы и проверочный SQL
- [x] 1.3 `.github/workflows/db-backup.yml`: расписание и ручной запуск, job `backup` (артефакт на 90 суток) и `restore-check` (`needs: backup`)
- [x] 1.4 `tests/test_db_backup.py`: без ключа ничего не читается, шифр расшифровывается ключом, старая копия — отказ, параметры `openssl` едины, retention 90, ключ не в `run`

## 2. Документация и проверка

- [x] 2.1 `infra/README.md`, «Копии вне сервера»: runbook восстановления, ключ вне GitHub, смена хранилища; комментарии в `infra/replica.tf`, `deploy.yml`, `recover-state.sh`; `docs/SYSTEM_DESIGN.md`
- [x] 2.2 Локальный прогон шифрования и восстановления на podman (design.md)

## 3. Алерт выноса

- [x] 3.1 Job `mark-success` в `db-backup.yml`: отметка `dmc268_offsite_backup_last_success_timestamp_seconds` в тот же textfile-том, что у `backup.sh`; environment `stand` без записи о деплое
- [x] 3.2 Алерт `OffsiteBackupStale` в `infra/observability/alerts.yml`, тесты promtool, строка в таблице алертов `infra/README.md`
- [x] 3.3 Сервис проверки восстановления — образ стенда `pgvector/pgvector:pg18` по digest
