# Tasks

## 1. База и брокер не видят смену env

- [x] 1.1 `infra/postgres.tf`, `infra/rabbitmq.tf`: `lifecycle { ignore_changes = [env] }`, в `env` только переменные инициализации тома
- [x] 1.2 Красный тест до правки: `test_a_new_secret_does_not_recreate_the_database_or_the_broker` (состав `env` и `ignore_changes` по тексту `infra/*.tf`)

## 2. Шаг паролей

- [x] 2.1 `.github/scripts/deploy/passwords.sh` — адаптер: вход новым паролем, `ALTER ROLE`/`change_password` через stdin `docker exec -i`, повторный вход; ожидание старта брокера
- [x] 2.2 `deploy.yml`: шаг `passwords` между `plan` и `migrate`, `timeout-minutes: 5`; `scripts/deploy_report.py`: шаг в `STEPS`, тексты сторожа
- [x] 2.3 Красные тесты до правки: `tests/test_deploy_passwords.py` — настоящий bash и sh контейнера с подложными `docker`, `psql`, `rabbitmqctl`

## 3. Репетиция и документация

- [x] 2.4 Ревью: пароль только `[A-Za-z0-9._~-]`; `\password` вместо открытого пароля в запросе (и у `replication-setup`); pull образа — свой шаг до паролей; смена имён роли и пользователя — `postcondition`; тесты ловят `ALTER ROLE` и пропавший `ON_ERROR_STOP` (проверено мутацией)
- [x] 3.1 Репетиция на `docker:29-dind`: переход с ветки #220, смена пароля базы, брокера и `replicator`, чистый сервер; окно отказа замерено
- [x] 3.2 `infra/README.md`, «Смена паролей»; `.agents/rules/backend.md`, «Деплой»
