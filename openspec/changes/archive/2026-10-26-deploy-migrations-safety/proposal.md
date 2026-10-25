# Proposal

## Why

Adversarial-ревью деплоя (#213), воспроизведено на `docker:29-dind` (Docker 29.9.2) и OpenTofu 1.13.2:

1. Summary врёт об упавших миграциях: ревизии 0013 (таблица) и 0014 (`SELECT 1/0`) — alembic гонит upgrade одной транзакцией, база осталась на 0012, а summary пишет «Применено ревизий: 2, после — 0014»: считает строки `Running upgrade`, не глядя на исход шага.
2. Шаг «Поднять базу и брокер» — `tofu apply -auto-approve -target=…` без плана и сторожа. Смена `TF_VAR_postgres_password` (или памяти, `log_opts`, env) — `postgres must be replaced`, а без `destroy_grace_seconds` замена — `kill 2` → `kill 9`, код 137. Пароль в существующем томе не меняется (`POSTGRES_PASSWORD` читается только при инициализации), и миграции падают `password authentication failed`. Раздел «План OpenTofu» summary этой замены не показывал.
3. У миграций нет ни `lock_timeout`, ни таймаута шага: `ALTER TABLE` на горячей таблице за открытой транзакцией старого API ждёт без ограничения, все чтения таблицы встают за ним; шаг — до 60 мин job'а, а оставшийся после него контейнер `dmc268-migrations` роняет следующий деплой на занятом имени.

## What Changes

- `scripts/deploy_report.py`: `migrations(log, outcome)` — при исходе шага `failure` применено 0, упавшая ревизия — последняя начатая, база — на ревизии до первой. Summary: «миграции откатены, применено 0, упала на ревизии X»; упал до первой ревизии — «схема не менялась». Исход шага приходит из `STEP_RESULTS` (уже в env шага summary, без `${{ }}` в `run`).
- Сторож плана: `stateful_deletes` — замена или удаление `docker_container.postgres`/`docker_container.rabbitmq` роняет шаг до apply, с адресом и ссылкой на runbook (#161). Действует на оба плана.
- Осознанный обход: `workflow_dispatch` с boolean `allow_stateful_replace` (по умолчанию `false`). Значение доходит до сторожа только из ручного запуска — `ALLOW_STATEFUL_REPLACE` в env шагов infra и plan выражением `github.event_name == 'workflow_dispatch' && inputs.allow_stateful_replace`; замена базы и брокера тогда — `::warning::` и пометка в summary с `github.actor`. Тома флаг не открывает.
- Новый `.github/scripts/deploy/infra.sh`: targeted-план в `infra/infra.tfplan`, `tofu show -json` трубой в `deploy_report.py plan infra` (`plan-infra.json`), apply ровно этого файла, файл удаляется на выходе. Summary показывает оба плана.
- `infra/postgres.tf`, `infra/rabbitmq.tf`: `destroy_grace_seconds = 60` — для ручной замены; добавление атрибута — обновление на месте.
- `alembic/env.py`: `SET lock_timeout = '5s'`, `SET statement_timeout = '2min'` в `run_migrations_online`; offline-SQL без них.
- `deploy.yml`: `timeout-minutes: 10` у шага миграций; `migrate.sh` удаляет остаток `dmc268-migrations` перед запуском и в trap на выходе.

## Capabilities

### Modified Capabilities

- `backend-delivery`: развёртывание не пересоздаёт базу и брокер; шаг миграций ограничен по времени; отчёт не приписывает упавшему шагу миграций применённых ревизий.
- `database-migrations`: миграция не ждёт блокировку бесконечно.

## Impact

- `.github/workflows/deploy.yml`, `.github/scripts/deploy/` (`infra.sh` — новый; `migrate.sh`, `plan.sh`), `scripts/deploy_report.py`, `alembic/env.py`, `infra/postgres.tf`, `infra/rabbitmq.tf`, `infra/tests/main.tftest.hcl`.
- Тесты: `tests/test_deploy_report.py`, фикстуры `tests/fixtures/deploy/plan-infra-*.json`, `tests/db/test_alembic_env.py`.
- `infra/README.md`, `docs/BACKEND_ARCHITECTURE.md`, скилл `alembic-migration`.
- Правка конфигурации базы или брокера автоматическим деплоем не катится: она падает на стороже, выкатывают её ручным запуском с `allow_stateful_replace` (PR #130 — `command` postgres, #102 — реплика). Ротация пароля — #161, вне этого change.
