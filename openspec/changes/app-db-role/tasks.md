## 1. Роль приложения

- [x] 1.1 Миграция 0023: роль `dmc268_app`, DML на таблицы и последовательности, `DEFAULT PRIVILEGES`, откат
- [x] 1.2 `alembic/role_passwords.py`: пароли `dmc268_monitor` и `dmc268_app` после миграций; `migrate.sh`, summary
- [x] 1.3 `infra/`: `postgres_app_password` с проверкой, `DATABASE_URL` api и worker — ролью приложения, `migrate_env` — владельцем; `deploy.yml`
- [x] 1.4 `docker-compose.yml`: api и worker — `dmc268_app`, migrate — владельцем
- [x] 1.5 Тесты: `tests/db/test_app_role.py`, tofu-тесты адресов и проверки пароля, тесты шага паролей

## 2. Документация

- [x] 2.1 `infra/README.md`: таблица секретов с владельцами и ротацией, «Смена паролей» под новую роль
- [x] 2.2 `docs/BACKEND_ARCHITECTURE.md`, `.agents/rules/backend.md`

## 3. Проверка на стенде (после мёржа)

- [ ] 3.1 До мёржа заведён секрет `PG_APP_PASSWORD_DMC268_T1`. Первый деплой после мёржа: миграция 0023 и шаг паролей зелёные, `docker exec dmc268-api printenv DATABASE_URL` — роль `dmc268_app`, API и воркер проходят проверки, в `pg_stat_activity` соединения API и воркера — `usename = 'dmc268_app'`
