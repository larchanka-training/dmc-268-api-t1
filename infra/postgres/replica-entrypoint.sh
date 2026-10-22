#!/bin/sh
# Точка входа горячей реплики (dmc268-postgres-replica).
#
# Пустой том — первая сборка: копия основного сервера через pg_basebackup по
# слоту dmc268_replica. Ключ -R пишет standby.signal и primary_conninfo, так
# что дальше сервер стартует репликой сам. Непустой том — обычный рестарт:
# реплика продолжает поток с того места, где остановилась, слот держит WAL.
#
# Запускается от root, как и штатный entrypoint образа: каталог данных
# готовится здесь, а на пользователя postgres переходят gosu и сам
# docker-entrypoint.sh.
set -eu

: "${PGDATA:?PGDATA задаёт образ postgres}"
: "${PRIMARY_HOST:?}"
: "${REPLICATION_PASSWORD:?}"

if [ ! -s "$PGDATA/PG_VERSION" ]; then
    echo "replica: каталог данных пуст — копирую основной сервер ${PRIMARY_HOST}"
    mkdir -p "$PGDATA"
    chown postgres:postgres "$PGDATA"
    chmod 700 "$PGDATA"

    # Основной сервер мог ещё не принять соединения: контейнер стартует по
    # depends_on, а он готовности не ждёт.
    attempt=0
    until gosu postgres env PGPASSWORD="$REPLICATION_PASSWORD" pg_basebackup \
        --host="$PRIMARY_HOST" --username=replicator \
        --pgdata="$PGDATA" --wal-method=stream \
        --slot=dmc268_replica --write-recovery-conf --checkpoint=fast; do
        attempt=$((attempt + 1))
        if [ "$attempt" -ge 30 ]; then
            echo "replica: pg_basebackup не прошёл за 30 попыток" >&2
            exit 1
        fi
        # Недописанная копия не должна выглядеть готовой на следующей попытке.
        find "$PGDATA" -mindepth 1 -delete
        sleep 2
    done
    echo "replica: копия снята, стартую в режиме standby"
fi

exec docker-entrypoint.sh "$@"
