# Tasks

## 1. Ожидание брокера

- [x] 1.1 `wait = true`, `wait_timeout = 120` у `docker_container.rabbitmq`
- [x] 1.2 Убрать цикл `await_startup` из `passwords.sh`; проверка `docker exec dmc268-rabbitmq true` до первой смены пароля остаётся (тест `test_a_missing_container_changes_no_password`)
- [x] 1.3 `tofu test`: у брокера `wait = true` и healthcheck, в том же run, что у базы (`database_ready_before_migrations`)
- [x] 1.4 Провайдер docker 4.6.0 на podman: контейнер с `wait = false`, затем `wait = true`: план `updated in-place`, id контейнера тот же
- [x] 1.5 `infra/README.md`: что делать, если контейнер не стал healthy за `wait_timeout` (tainted, `tofu untaint`, а не повторная замена)
