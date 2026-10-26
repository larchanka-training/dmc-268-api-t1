# Proposal

## Why

Пароль базы и брокера на стенде сменить нечем (#161). `POSTGRES_PASSWORD` и `RABBITMQ_DEFAULT_PASS` образы читают только на пустом томе: новый секрет доходит до API, воркера и миграций, но не до базы и брокера. До #220 смена `TF_VAR_postgres_password` пересоздавала базу (`postgres must be replaced`) с прежним паролем в томе, и миграции падали `password authentication failed`; после #220 сторож такой план отклоняет, и смену пароля не выкатить ничем — ни обычным деплоем, ни ручным с `allow_stateful_replace`. Цель задачи: смена пароля базы с простоем не больше одного деплоя.

## What Changes

- `infra/postgres.tf`, `infra/rabbitmq.tf`: `lifecycle { ignore_changes = [env] }` у `docker_container.postgres` и `docker_container.rabbitmq`. В их `env` только переменные инициализации тома; смена секрета контейнер больше не пересоздаёт.
- Новый шаг деплоя «Выставить пароли базы и брокера» (`.github/scripts/deploy/passwords.sh`) между планом и миграциями: входит новым паролем; не вошёл — `ALTER ROLE` через `psql` по локальному сокету и `rabbitmqctl change_password` изнутри контейнеров, затем входит снова. Пароль — только через stdin `docker exec -i`, сессия psql не пишет его в лог сервера и в вывод.
- `scripts/deploy_report.py`: шаг в `STEPS`; сообщения сторожа и summary больше не отсылают смену пароля к ручной процедуре.
- `infra/README.md`, «Смена паролей»: процедура для каждого из четырёх секретов, проверка после, откат, утечка. `.agents/rules/backend.md`, «Деплой»: строка о смене пароля.

## Capabilities

### Modified Capabilities

- `backend-delivery`: смена пароля базы и брокера — обычное развёртывание; сторож базы и брокера пароль не касается.

## Impact

- `.github/workflows/deploy.yml`, `.github/scripts/deploy/passwords.sh` (новый), `.github/scripts/deploy/infra.sh` (комментарий), `scripts/deploy_report.py`, `infra/postgres.tf`, `infra/rabbitmq.tf`.
- Тесты: `tests/test_deploy_passwords.py` (новый), `tests/test_deploy_report.py`.
- Первый деплой после мёржа: план `No changes` у базы и брокера (проверено переходом с ветки #220 на dind), шаг паролей — «совпадает с секретом».
- Роль `dmc268_monitor` (`PG_MONITOR_PASSWORD_DMC268_T1`) приходит с PR #130 и свой пароль переустанавливает шагом миграций; этот change её не трогает.
