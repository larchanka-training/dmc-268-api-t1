## 1. Экземпляры воркера

- [x] 1.1 `worker_replicas` с проверкой от 1 до 4, `local.worker_names`, `for_each` у `docker_container.worker`, `moved` для первого экземпляра
- [x] 1.2 `tofu state mv` старого адреса в `infra.sh` до targeted-плана, только пока старый адрес в состоянии; тесты на подложном `tofu`
- [x] 1.3 `infra/tests`: один экземпляр сохраняет имя, два экземпляра отличаются только именем и `service.instance.id`, 0, 5 и 1,5 отклоняются

## 2. Деплой

- [x] 2.1 `TF_VAR_worker_replicas` в `env` job'а `deploy`
- [x] 2.2 `check-worker.sh`: каждый экземпляр по имени, их точное число; тесты с подложными `ssh`, `date`, `sleep`
- [x] 2.3 Окно остановки против `timeout-minutes` job'а: экземпляры пересоздаются параллельно — комментарий в `deploy.yml` и `app.tf`, раздел README

## 3. Наблюдаемость и статус

- [x] 3.1 `dmc268:worker_expected` в `workers.yml` (compose, CI) и из `local.worker_rules` на стенде; `ContainerDown` по каждому ожидаемому экземпляру; тесты promtool и `observability.tftest.hcl`
- [x] 3.2 `service` у `ContainerMemoryNearLimit` без номера экземпляра; тест promtool
- [x] 3.3 `service_status.py`: экземпляр воркера — по шаблону имени, `ContainerDown` экземпляра — `degraded`
- [x] 3.4 Дашборд: предел пула сложен по экземплярам

## 4. Параллельная обработка

- [x] 4.1 `tests/queue/test_worker_replicas.py`: две задачи одновременно — два воркера параллельно, на живом RabbitMQ

## 5. Документация

- [x] 5.1 `infra/README.md`, «Масштабирование воркеров»: память, соединения базы, окно остановки, алерты, локальная проверка
- [x] 5.2 `docs/SYSTEM_DESIGN.md`, `docs/BACKEND_ARCHITECTURE.md`, `terraform.tfvars.example`
