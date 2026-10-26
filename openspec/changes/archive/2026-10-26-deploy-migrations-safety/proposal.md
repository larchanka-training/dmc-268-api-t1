# Proposal

## Why

Adversarial-ревью деплоя (#213), воспроизведено на `docker:29-dind` (Docker 29.9.2) и OpenTofu 1.13.2:

1. Summary врёт об упавших миграциях: ревизии 0013 (таблица) и 0014 (`SELECT 1/0`) — alembic гонит upgrade одной транзакцией, база осталась на 0012, а summary пишет «Применено ревизий: 2, после — 0014»: считает строки `Running upgrade`, не глядя на исход шага.
2. Шаг «Поднять базу и брокер» — `tofu apply -auto-approve -target=…` без плана и сторожа. Смена `TF_VAR_postgres_password` (или памяти, `log_opts`, env) — `postgres must be replaced`, а без `destroy_grace_seconds` замена — `kill 2` → `kill 9`, код 137. Пароль в существующем томе не меняется (`POSTGRES_PASSWORD` читается только при инициализации), и миграции падают `password authentication failed`. Раздел «План OpenTofu» summary этой замены не показывал.
3. У миграций нет ни `lock_timeout`, ни таймаута шага: `ALTER TABLE` на горячей таблице за открытой транзакцией старого API ждёт без ограничения, все чтения таблицы встают за ним; шаг — до 60 мин job'а, а оставшийся после него контейнер `dmc268-migrations` роняет следующий деплой на занятом имени.

## What Changes

- `scripts/deploy_report.py`: `migrations(log, outcome)` — при исходе шага `failure` применено 0, упавшая ревизия — последняя начатая, база — на ревизии до первой. Summary: «миграции откатены, применено 0, упала на ревизии X»; упал до первой ревизии — «схема не менялась». Исход шага приходит из `STEP_RESULTS` (уже в env шага summary, без `${{ }}` в `run`).
- Сторож плана: `stateful_deletes` — замена или удаление `docker_container.postgres`/`docker_container.rabbitmq` роняет шаг до apply, с адресом и ссылкой на runbook (#161). Действует на оба плана. Контейнер узнаётся и по имени на хосте (`dmc268-postgres`, `dmc268-rabbitmq`): переименование ресурса без `moved` сторож не обходит.
- Осознанный обход: `workflow_dispatch` с boolean `allow_stateful_replace` (по умолчанию `false`). Значение доходит до сторожа только из ручного запуска — `ALLOW_STATEFUL_REPLACE` в env шагов infra и plan выражением `github.event_name == 'workflow_dispatch' && github.run_attempt == 1 && inputs.allow_stateful_replace` (Re-run флага не получает); замена базы и брокера тогда — `::warning::` и пометка в summary с `github.triggering_actor`. Тома флаг не открывает.
- Новый `.github/scripts/deploy/infra.sh`: targeted-план в `infra/infra.tfplan`, `tofu show -json` трубой в `deploy_report.py plan infra` (`plan-infra.json`), apply ровно этого файла, оба с `-lock-timeout=2m`, как plan.sh и apply.sh в #215; файл удаляется на выходе, а после отмены или таймаута шага — в `summary.sh`. Summary показывает оба плана.
- `destroy_grace_seconds = 60` у postgres и rabbitmq — для ручной замены; после слияния с develop он и `stop_timeout` — из #102, здесь только комментарии в `infra/postgres.tf`.
- `alembic/env.py`: `SET lock_timeout = '5s'`, `SET statement_timeout = '2min'` в `run_migrations_online`; offline-SQL без них.
- После упавшего upgrade `migrate.sh` спрашивает `alembic current`, и summary показывает ревизию базы по нему, а не по логу (ревью #220). Не дождалась миграция блокировки, а база на исходной ревизии — шаг повторяет upgrade: до 3 попыток с паузой 15 с, в пределах `timeout-minutes`. Общие аргументы обоих `docker run` — один массив, сеть в одном месте.
- `deploy.yml`: `timeout-minutes: 10` у шага миграций; `migrate.sh` удаляет остаток `dmc268-migrations` перед запуском и в trap на выходе.

## Capabilities

### Modified Capabilities

- `backend-delivery`: развёртывание не пересоздаёт базу и брокер; шаг миграций ограничен по времени; отчёт не приписывает упавшему шагу миграций применённых ревизий.
- `database-migrations`: миграция не ждёт блокировку бесконечно.

## Impact

- `.github/workflows/deploy.yml`, `.github/scripts/deploy/` (`infra.sh` — новый; `migrate.sh`, `plan.sh`, `summary.sh`), `scripts/deploy_report.py`, `alembic/env.py`, `infra/postgres.tf` (комментарии).
- Тесты: `tests/test_deploy_report.py`, `tests/test_infra_isolation.py`, `tests/test_stack_descriptions.py`, `tests/shell_text.py`, фикстуры `tests/fixtures/deploy/plan-infra-*.json`, `tests/db/test_alembic_env.py`.
- `infra/README.md`, `docs/BACKEND_ARCHITECTURE.md`, скилл `alembic-migration`.
- Правка конфигурации базы или брокера автоматическим деплоем не катится: она падает на стороже, выкатывают её ручным запуском с `allow_stateful_replace` (PR #130 — `command` postgres, #102 — реплика). Ротация пароля — #161, вне этого change.
