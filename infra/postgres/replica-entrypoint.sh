#!/bin/sh
# Точка входа горячей реплики (dmc268-postgres-replica).
#
# Пароль роли replicator живёт только в passfile, который пишется заново на
# каждом старте: не в каталоге данных (primary_conninfo от `pg_basebackup -R`
# хранил бы его навсегда) и не в argv. Смена секрета пересоздаёт контейнер —
# новый пароль доходит до walreceiver без пересборки тома.
#
# Реплика пересобирается с нуля (pg_basebackup), когда:
#   - каталог данных пуст или копия не дописана;
#   - основной сервер — другой кластер (его том заведён заново);
#   - слота нет или он потерян (wal_status = lost после max_slot_wal_keep_size).
# Решение принимается только по ответу основного сервера: недоступный основной
# сервер — не повод стирать реплику, она просто подождёт его, как обычно.
#
# Копия снимается во временный каталог и переименовывается в PGDATA только
# целиком: убитый посреди pg_basebackup контейнер не оставит каталог, который
# выглядит готовым.
#
# Запускается от root, как и штатный entrypoint образа; на пользователя postgres
# переходят gosu и сам docker-entrypoint.sh.
set -eu

: "${PGDATA:?PGDATA задаёт образ postgres}"
: "${PRIMARY_HOST:?}"
: "${REPLICATION_PASSWORD:?}"

slot=dmc268_replica
passfile=/var/lib/postgresql/.pgpass-replication
conninfo="host=$PRIMARY_HOST user=replicator passfile=$passfile application_name=$slot"

as_postgres() { gosu postgres "$@"; }

write_passfile() {
    # В .pgpass двоеточие и обратная косая черта экранируются.
    escaped="$(printf '%s' "$REPLICATION_PASSWORD" | sed 's/[\\:]/\\&/g')"
    (umask 077 && printf '%s:5432:*:replicator:%s\n' "$PRIMARY_HOST" "$escaped" > "$passfile")
    chown postgres:postgres "$passfile"
}

# 0 — пересобрать, 1 — оставить как есть (в том числе когда основной сервер
# не ответил и решить нельзя).
needs_rebuild() {
    if [ ! -s "$PGDATA/PG_VERSION" ]; then
        echo "replica: каталог данных пуст"
        return 0
    fi
    if ! primary_id="$(as_postgres psql "$conninfo replication=true" -XtA -c IDENTIFY_SYSTEM 2>/dev/null | cut -d'|' -f1)" \
        || [ -z "$primary_id" ]; then
        echo "replica: основной сервер не ответил — каталог данных не трогаю"
        return 1
    fi
    local_id="$(as_postgres pg_controldata "$PGDATA" | sed -n 's/^Database system identifier: *//p')"
    if [ "$primary_id" != "$local_id" ]; then
        echo "replica: основной сервер — другой кластер ($primary_id, у реплики $local_id)"
        return 0
    fi
    if ! wal_status="$(as_postgres psql "$conninfo dbname=postgres" -XtA \
        -c "select coalesce(wal_status, '') from pg_replication_slots where slot_name = '$slot'" 2>/dev/null)"; then
        echo "replica: не удалось прочитать слот — каталог данных не трогаю"
        return 1
    fi
    case "$wal_status" in
        "") echo "replica: слота $slot нет"; return 0 ;;
        lost) echo "replica: слот $slot потерян (max_slot_wal_keep_size)"; return 0 ;;
    esac
    return 1
}

rebuild() {
    partial="$PGDATA.partial"
    mkdir -p "$(dirname "$PGDATA")"
    attempt=0
    while :; do
        rm -rf "$partial"
        mkdir -p "$partial"
        chown postgres:postgres "$partial"
        chmod 700 "$partial"
        # Неактивный слот с прошлой жизни реплики (в том числе потерянный)
        # удаляется: pg_basebackup --create-slot заводит его заново.
        if as_postgres psql "$conninfo dbname=postgres" -XtAq -v ON_ERROR_STOP=1 \
            -c "select pg_drop_replication_slot(slot_name) from pg_replication_slots where slot_name = '$slot' and not active" \
            && as_postgres pg_basebackup --dbname="$conninfo" --pgdata="$partial" \
                --wal-method=stream --slot="$slot" --create-slot --checkpoint=fast; then
            break
        fi
        attempt=$((attempt + 1))
        if [ "$attempt" -ge 30 ]; then
            echo "replica: pg_basebackup не прошёл за 30 попыток" >&2
            exit 1
        fi
        sleep 2
    done
    touch "$partial/standby.signal"
    chown postgres:postgres "$partial/standby.signal"
    rm -rf "$PGDATA"
    mv "$partial" "$PGDATA"
    echo "replica: копия снята, стартую в режиме standby"
}

write_passfile
if needs_rebuild; then
    echo "replica: пересобираю с основного сервера $PRIMARY_HOST"
    rebuild
fi

# Параметры реплики — аргументами, а не в postgresql.auto.conf: так они
# переписываются на каждом старте вместе с passfile.
exec docker-entrypoint.sh "$@" \
    -c "primary_conninfo=$conninfo" \
    -c "primary_slot_name=$slot"
