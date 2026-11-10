# Proposal

## Why

`.github/scripts/deploy/passwords.sh` перед сменой пароля брокера до двух минут крутит `rabbitmqctl await_startup` со `sleep 2`: у контейнера брокера не было `wait`. У базы это уже решено штатно, `wait = true` в `infra/postgres.tf`. Аудит 2026-11-04, #345. Healthcheck брокера после #342 зеленеет, только когда узел принимает AMQP, так что `wait` ждёт именно того, что нужно смене пароля.

## What Changes

- `wait = true` и `wait_timeout = 120` у `docker_container.rabbitmq`: две минуты, как было у цикла.
- Цикл `await_startup` из `passwords.sh` удаляется. Вместо его побочной роли остаётся `docker exec dmc268-rabbitmq true`: оба контейнера проверяются до первой смены пароля, как и раньше.

## Capabilities

### New Capabilities

Нет.

### Modified Capabilities

Нет.

`skip_specs: true`: меняется обвязка деплоя, а не поведение сервиса.

## Impact

- `infra/rabbitmq.tf`, `.github/scripts/deploy/passwords.sh`, `infra/README.md`, тесты деплоя.
- В провайдере docker 4.6.0 у `wait` и `wait_timeout` нет `ForceNew`: на стенде правка на месте, контейнер брокера не пересоздаётся и `allow_stateful_replace` не нужен.
