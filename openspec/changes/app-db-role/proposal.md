## Why

#101. API и воркер входят в базу ролью `dmc268` — владельцем базы и суперпользователем кластера
(`POSTGRES_USER` образа). Ошибка или SQL-инъекция в приложении получила бы всё: DDL, чтение
файлов сервера, роли кластера. Секретам стенда нет общей таблицы с владельцами и порядком смены.

## What Changes

- Миграция 0023 создаёт роль `dmc268_app` (`LOGIN`, без `SUPERUSER`, `CREATEDB`, `CREATEROLE`) с
  `SELECT`, `INSERT`, `UPDATE`, `DELETE` на таблицы и `USAGE`, `SELECT` на последовательности схемы;
  `ALTER DEFAULT PRIVILEGES` даёт те же права на таблицы следующих миграций. `alembic_version`
  приложению закрыта.
- API и воркер входят ролью `dmc268_app` (`local.app_env`, `docker-compose.yml`), миграции — как
  прежде владельцем (`migrate_env`).
- Пароль роли — новый секрет `PG_APP_PASSWORD_DMC268_T1` (`TF_VAR_postgres_app_password`): не короче
  16 символов, печатный ASCII без пробела, иначе отказ на `plan`. Ставит его роли шаг миграций:
  `alembic/monitor_password.py` переименован в `alembic/role_passwords.py` и сводит с секретами пароли
  обеих ролей, `dmc268_monitor` и `dmc268_app`. Смена пароля — секрет → деплой.
- `infra/README.md`: таблица секретов с владельцами и ротацией, «Смена паролей» — под новую роль.

## Capabilities

### Modified Capabilities

- `backend-delivery`: приложение работает с базой под ролью без прав на схему.

## Impact

- `alembic/versions/0023_add_app_role.py`, `alembic/role_passwords.py`, `.github/scripts/deploy/migrate.sh`,
  `.github/workflows/deploy.yml`, `scripts/deploy_report.py`, `infra/variables.tf`, `infra/locals.tf`,
  `infra/outputs.tf`, `infra/tests/*.tftest.hcl`, `docker-compose.yml`, документация.
- Тесты: `tests/db/test_app_role.py` (новый), `tests/test_deploy_report.py`,
  `tests/infrastructure/test_observability_stack.py`, `tests/chaos/test_migrate_lock_chaos.py`.
- **До мёржа** нужен секрет `PG_APP_PASSWORD_DMC268_T1`: без него `plan` первого же деплоя падает.
- Порядок первого деплоя: миграция 0023 и шаг паролей ролей идут до полного apply, который пересоздаёт
  `api` и `worker` с новым `DATABASE_URL`.
- Ревизия 0022 занята Stripe (#413), поэтому ревизия роли приложения: 0023 с `down_revision = '0022'`.
