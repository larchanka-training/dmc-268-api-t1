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

## 3. Правки по ревью

- [x] 3.1 Пароль репликации — в `passfile`, который реплика пишет на каждом старте; `primary_conninfo` и `primary_slot_name` — аргументами, без `pg_basebackup -R`
- [x] 3.2 Реплика сама пересобирается: пустой или недописанный том, отсутствующий или потерянный слот; слот заводит `pg_basebackup --create-slot` (другой кластер — см. 3.8)
- [x] 3.3 Копия снимается во временный каталог и переименовывается целиком; `init` у реплики
- [x] 3.4 Healthcheck реплики — `pg_stat_wal_receiver.status = streaming`; бэкап с основного сервера, если реплика не получает поток; хранение по `-mmin`, чистка `*.partial`
- [x] 3.5 `replication-setup` перезапускается и при пересоздании основной базы; пароль через `\getenv`, не в argv
- [x] 3.6 Проверка деплоя — по `application_name = dmc268_replica`, имена из окружения контейнера, `ConnectTimeout`, предел по времени
- [x] 3.7 `tests/test_infra_isolation.py` сверяет точный набор сетей у каждого контейнера
- [x] 3.8 Другой кластер основного сервера — реплика не стирает каталог, а завершается с ошибкой; `replace_triggered_by` от тома основной базы у реплики убран
- [x] 3.9 У `pg_basebackup` своё `application_name` (`dmc268_basebackup`), в conninfo — `connect_timeout=5`; растущая пауза между попытками сборки
- [x] 3.10 Деплой перезапускает unhealthy-реплику перед проверкой потока; README — пересборка требует места под две копии, unhealthy-реплика значит копии с основного сервера

- [x] 3.11 `develop` с раскладкой `infra/` по файлам доменов (#196) и скриптами деплоя (#197): изменения перенесены в `network.tf`, `postgres.tf`, `rabbitmq.tf`, `app.tf`, новый `replica.tf`; проверка реплики — `.github/scripts/deploy/check-replica.sh`; `test_infra_isolation` читает `infra/*.tf`
- [x] 3.12 `postgres`, реплика и `rabbitmq`: `stop_timeout` и `destroy_grace_seconds` по 60 с
- [x] 3.13 `infra/tests/*.tftest.hcl`: раскладка сетей по плану, нет портов у базы и брокера, грейс 60 с, пароль реплики не в аргументах, новые контейнеры — в проверках привилегий, памяти и логов; пустой пароль реплики отклоняется

## 4. Приёмка

- [x] 4.1 Секрет `PG_REPLICATION_PASSWORD_DMC268_T1` заведён в настройках репозитория
- [ ] 4.2 Деплой проходит: `/health` отвечает, воркер устойчив, реплика в `streaming`
- [ ] 4.3 Повторный деплой того же коммита даёт `No changes`
- [ ] 4.4 На стенде: `docker port` у `dmc268-postgres` и `dmc268-rabbitmq` пуст; из контейнера в `dmc268-backend` нет выхода в интернет; в томе `dmc268-postgres-backups` есть свежая копия, `pg_restore --list` её читает
- [ ] 4.5 Вход через GitHub и вебхук на стенде работают (API ходит в GitHub через сеть входа, воркер — через `egress`)

## Примечания к выполнению

1.x и 2.x проверены локально (Docker 29.8.2, OpenTofu 1.12.6 в контейнере): `tofu fmt -check` и `validate` чистые; `apply` части с базой (сети, тома, `postgres`, `replication-setup`, реплика, бэкап) на локальный Docker с локальным состоянием. Поток `streaming` по слоту `dmc268_replica`, `pg_is_in_recovery() = t`, запись на основном сервере видна на реплике, `INSERT` на реплике отклоняется; рестарт реплики продолжает поток без пересборки; копия снята и читается `pg_restore --list`; контейнер в `dmc268-backend` не достаёт до `1.1.1.1` (`Network is unreachable`); `docker port dmc268-postgres` пуст; повторный `plan` — `No changes`.

`api`, `worker` и `migrate` локально не поднимались (нужен образ из ghcr); их сети проверяет `tests/test_infra_isolation.py`, а работу — приёмка 4.2 и 4.5. Пункты 4.2–4.5 выполнимы только после мёржа в `develop`.

Правки по ревью (3.x) проверены так же, локально, с `postgres_max_slot_wal_keep_size = 64MB`:
- свежая сборка: поток держит walsender `application_name = dmc268_replica`, в `postgresql.auto.conf` пароля нет, `passfile` — `0600 postgres`, ни в одном процессе обоих контейнеров пароля в argv нет;
- ротация пароля: роль и реплика пересозданы, реплика стартует без пересборки тома и снова в `streaming`;
- потерянный слот: реплика остановлена, нагнано больше 64 МБ WAL, слот `wal_status = lost`; после старта реплика сама пишет «слот потерян», пересобирается, слот снова `reserved`, данные на месте;
- новый том основной базы (`-replace` тома и контейнера): `replication-setup` перезапущен, реплика пересоздана, пишет «основной сервер — другой кластер», пересобирается, `system_identifier` совпадает;
- реплика без потока (`replicator NOLOGIN`): healthcheck реплики падает, бэкап снимается с основного сервера, `*.partial` чистятся при старте; после `LOGIN` поток восстанавливается сам;
- повторный `plan` — `No changes`.

На OpenTofu 1.12.6 apply с единственным `-target` на контейнер бэкапа не применил замену, вызванную `replace_triggered_by` (план её показывал, apply — `0 added`). С явными `-target` на все затронутые ресурсы замена применяется; в деплое `-target` не используется.

Правки по второму ревью (3.8–3.10) проверены локально, на 2 млн строк в основной базе:
- другой кластер: том основной базы заменён (`-replace` тома и контейнера), реплика не пересоздана; после её рестарта — «основной сервер — другой кластер … каталог данных НЕ стираю», выход с кодом 1, каталог реплики (589 МБ) на месте, `.partial` нет; ручная пересборка из README (удалить том, apply) даёт `streaming`;
- имя `pg_basebackup`: при `--dbname="… application_name=dmc268_replica application_name=dmc268_basebackup"` оба walsender'а копии видны как `dmc268_basebackup` (`backup`), `dmc268_replica` — только настоящая реплика;
- зависший основной сервер (`docker pause`): рестарт реплики — «основной сервер не ответил — каталог данных не трогаю», через 6 с принимает соединения на чтение; после `unpause` поток восстанавливается сам.

Перенос на раскладку `infra/` по файлам (3.11–3.13) проверен локально на OpenTofu 1.13.2: `tofu fmt -check`, `validate`, `tofu test` — 19 passed; apply части с базой на Docker — реплика в `streaming`, у `postgres` и `rabbitmq` только сеть `dmc268-backend` и нет портов. `-replace` контейнеров `postgres`, реплики и `rabbitmq` при 200 тыс. строк: новый `postgres` пишет «database system was shut down» (без «not properly shut down» и восстановления после аварии), реплика — «shut down in recovery» и продолжает поток без пересборки, данные на месте.
