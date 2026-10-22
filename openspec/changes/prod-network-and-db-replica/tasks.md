# Tasks

## 1. Сети

- [x] 1.1 `docker_network.backend` (`dmc268-backend`, `internal`) и `docker_network.egress` (`dmc268-egress`); `dmc268` остаётся сетью входа
- [x] 1.2 `postgres`, `rabbitmq`, `migrate` — только `backend`; `api` — `dmc268` и `backend`; `worker` — `backend` и `egress`
- [x] 1.3 Убрать порты `postgres` и `rabbitmq` на хосте, переменные `internal_bind_ip`, `postgres_port`, `rabbitmq_port`, выходы `postgres_url`, `rabbitmq_url`

## 2. Реплика и копии

- [x] 2.1 `infra/postgres/pg_hba.conf` вне каталога данных, `hba_file` и `max_slot_wal_keep_size` у основного сервера
- [x] 2.2 `dmc268-replication-setup`: роль `replicator` и слот `dmc268_replica`, идемпотентно, с постусловием на код возврата
- [x] 2.3 `dmc268-postgres-replica`: `pg_basebackup` на пустом томе, standby, healthcheck на `pg_is_in_recovery()`
- [x] 2.4 `dmc268-postgres-backup`: `pg_dump -Fc` с реплики раз в сутки, хранение `backup_keep_days`, healthcheck на свежесть копии
- [x] 2.5 `deploy.yml`: `TF_VAR_postgres_replication_password` из секрета, шаг проверки потока реплики
- [x] 2.6 `infra/README.md`, `terraform.tfvars.example`, `tests/test_infra_isolation.py`

## 3. Приёмка

- [ ] 3.1 Секрет `PG_REPLICATION_PASSWORD_DMC268_T1` заведён в настройках репозитория
- [ ] 3.2 Деплой проходит: `/health` отвечает, воркер устойчив, реплика в `streaming`
- [ ] 3.3 Повторный деплой того же коммита даёт `No changes`
- [ ] 3.4 На стенде: `docker port` у `dmc268-postgres` и `dmc268-rabbitmq` пуст; из контейнера в `dmc268-backend` нет выхода в интернет; в томе `dmc268-postgres-backups` есть свежая копия, `pg_restore --list` её читает
- [ ] 3.5 Вход через GitHub и вебхук на стенде работают (API ходит в GitHub через сеть входа, воркер — через `egress`)

## Примечания к выполнению

1.x и 2.x проверены локально (Docker 29.8.2, OpenTofu 1.12.6 в контейнере): `tofu fmt -check` и `validate` чистые; `apply` части с базой (сети, тома, `postgres`, `replication-setup`, реплика, бэкап) на локальный Docker с локальным состоянием. Поток `streaming` по слоту `dmc268_replica`, `pg_is_in_recovery() = t`, запись на основном сервере видна на реплике, `INSERT` на реплике отклоняется; рестарт реплики продолжает поток без пересборки; копия снята и читается `pg_restore --list`; контейнер в `dmc268-backend` не достаёт до `1.1.1.1` (`Network is unreachable`); `docker port dmc268-postgres` пуст; повторный `plan` — `No changes`.

`api`, `worker` и `migrate` локально не поднимались (нужен образ из ghcr); их сети проверяет `tests/test_infra_isolation.py`, а работу — приёмка 3.2 и 3.5. Пункты 3.x выполнимы только после мёржа в `develop`.
