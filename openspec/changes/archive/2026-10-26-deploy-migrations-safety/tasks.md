# Tasks

## 1. Summary упавших миграций

- [x] 1.1 `scripts/deploy_report.py`: `migrations(log, outcome)` — чистая функция; при `failure` применено 0, упавшая ревизия и ревизия базы; `migrations_section` берёт исход из `STEP_RESULTS`
- [x] 1.2 Красный тест до правки: `test_failed_migrations_applied_nothing`, `test_summary_of_failed_migrations_does_not_claim_revisions`, упавший до первой ревизии

## 2. Targeted apply базы и брокера

- [x] 2.1 `scripts/deploy_report.py`: `stateful_deletes` — чистая функция над JSON плана; `plan infra` пишет `plan-infra.json`; `plan_section` — оба плана
- [x] 2.2 `.github/scripts/deploy/infra.sh`: `tofu plan -target=… -out=infra.tfplan`, сторож, `apply infra.tfplan`, файл удаляется на выходе; `deploy.yml` — шаг «Поднять базу и брокер» через скрипт
- [x] 2.3 `infra/postgres.tf`, `infra/rabbitmq.tf`: `destroy_grace_seconds = 60`; `infra/tests/main.tftest.hcl` — проверка
- [x] 2.4 Красный тест до правки: `test_targeted_plan_with_a_new_database_password_is_rejected` на настоящем `tofu show -json` со сменой пароля
- [x] 2.5 Репетиция на dind: добавление grace — обновление на месте; смена пароля — отказ, контейнер тот же; повтор — `No changes`

- [x] 2.6 Ручной обход: `workflow_dispatch` input `allow_stateful_replace`, `ALLOW_STATEFUL_REPLACE`/`DEPLOY_ACTOR` в env шагов infra и plan; сторож — предупреждение вместо отказа, тома защищены всегда; тесты с флагом и без

## 3. Тайм-ауты миграций

- [x] 3.1 `alembic/env.py`: `SET lock_timeout = '5s'`, `SET statement_timeout = '2min'` в `run_migrations_online`
- [x] 3.2 `deploy.yml`: `timeout-minutes: 10` у шага миграций; `migrate.sh`: `docker rm -f dmc268-migrations` перед запуском и в trap
- [x] 3.3 Красный тест до правки: `test_a_migration_waiting_for_a_lock_gives_up_after_lock_timeout` на живом PostgreSQL; offline-SQL без тайм-аутов
- [x] 3.4 `infra/README.md`, `docs/BACKEND_ARCHITECTURE.md`, скилл `alembic-migration`

## 4. Проверки

- [x] 4.1 ruff, lint-imports, mypy, pytest с `PYTEST_FAIL_ON_SKIP=1` и живыми PostgreSQL и RabbitMQ, `openspec validate --all --strict`, `tofu fmt`/`validate`/`test`, shellcheck 0.9.0, zizmor

## 5. Правки ревью #220

- [x] 5.1 `migrate.sh`: после упавшего upgrade — `docker rm -f` сироты и `alembic current` тем же образом и env-файлом под `timeout 60`, вывод в `migrate-current.log`, сбой — `migrate-current.error`; `deploy_report.py`: `current_revision`, `migrations(log, outcome, current)` — число применённых по ревизии базы, «ревизия неизвестна» без «откатено»
- [x] 5.2 `deploy.yml`: флаг — только у первой попытки (`github.run_attempt == 1`), `DEPLOY_ACTOR` — `github.triggering_actor`
- [x] 5.3 Тест: каждый `docker_container` на `docker_volume` из `infra/*.tf` — в `STATEFUL` и в `-target` `infra.sh`
- [x] 5.4 Нет `migrate.log` при упавшем шаге — «упал до alembic», а не «не запускался»
- [x] 5.5 Тест `SHOW lock_timeout`/`statement_timeout` в сессии миграций на живом PostgreSQL
- [x] 5.6 Правила `.agents/rules/backend.md` («Деплой», «Миграции») и строка в `AGENTS.md`
