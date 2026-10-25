## 1. Тесты (красные до правки)

- [x] 1.1 `infra/tests/main.tftest.hcl`: адреса и секреты в `migrate_env`, строки `migrate_env` есть у `api` и `worker`, свой `docker_image.worker` с той же ссылкой и `keep_locally`, `wait` у postgres; `migrate` убран из списков, `failed_migration_stops_deploy` удалён
- [x] 1.2 `infra/tests/hardening.tftest.hcl`: `migrate` убран из списков
- [x] 1.3 `tests/test_stack_descriptions.py`: шаг `migrate.sh` до полного `tofu apply`, `docker run --rm` с `alembic upgrade head`, ограничения процесса миграций; мутации конфигурации роняют проверки

## 2. Конфигурация стека

- [x] 2.1 `infra/outputs.tf`: sensitive `migrate_env` из `local.app_env`; хосты в `local.app_env` — ссылки на контейнеры базы и брокера
- [x] 2.2 `infra/app.tf`: `removed` для `docker_container.migrate` (`destroy = false`), `docker_image.worker`, зависимости `api` и `worker` без миграций
- [x] 2.3 `infra/postgres.tf`: `wait = true`

## 3. Деплой

- [x] 3.1 `.github/scripts/deploy/migrate.sh`: env-файл из `tofu output`, удаление `dmc268-migrate`, `docker run --rm` в сети `dmc268`
- [x] 3.2 `deploy.yml`: вход в `ghcr.io`, `apply -target` сети, базы и брокера, шаг миграций, полный `apply` без `-replace`
- [x] 3.3 `check-api.sh`: без логов `dmc268-migrate`

## 4. Документация

- [x] 4.1 `infra/README.md`: порядок деплоя, чистый сервер, `removed`, свой образ воркера
- [x] 4.2 Expand/contract в `.agents/skills/alembic-migration/SKILL.md` и `docs/BACKEND_ARCHITECTURE.md`

## 5. Проверка

- [x] 5.1 Локальный Docker: простой API до и после, bootstrap чистого Docker, упавшая миграция не трогает API, pull из реестра с паролем через вход на клиенте, повторный `plan` — `No changes`
- [x] 5.2 `tofu fmt`/`validate`/`test`, actionlint, zizmor, shellcheck, ruff, pytest, `openspec validate --all --strict`
