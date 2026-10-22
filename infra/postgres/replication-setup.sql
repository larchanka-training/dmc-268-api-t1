-- Роль и слот для горячей реплики. Выполняется одноразовым контейнером
-- dmc268-replication-setup на каждом деплое и обязан быть идемпотентным:
-- init-скрипты образа отрабатывают только на пустом томе, а том основной базы
-- заведён задолго до реплики.
--
-- Это не схема приложения (её меняет только Alembic), а объекты кластера:
-- роль и слот живут вне базы dmc268 и миграциям не принадлежат.
--
-- Пароль приходит переменной psql: `-v replication_password=...`.

\set ON_ERROR_STOP on

SELECT 'CREATE ROLE replicator'
WHERE NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'replicator')
\gexec

-- Пароль переустанавливается каждый раз: так смена секрета доходит до базы
-- обычным деплоем, без ручного ALTER ROLE.
ALTER ROLE replicator WITH LOGIN REPLICATION NOSUPERUSER PASSWORD :'replication_password';

-- Слот держит WAL, пока реплика его не забрала: без него реплика, отставшая
-- дольше wal_keep_size, потеряла бы поток и требовала бы пересборки. Объём,
-- который слот может удержать, ограничен max_slot_wal_keep_size основного
-- сервера — иначе мёртвая реплика заполнила бы диск.
SELECT pg_create_physical_replication_slot('dmc268_replica')
WHERE NOT EXISTS (
    SELECT FROM pg_replication_slots WHERE slot_name = 'dmc268_replica'
);
