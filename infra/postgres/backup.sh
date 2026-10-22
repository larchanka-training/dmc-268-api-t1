#!/bin/sh
# Резервные копии базы (dmc268-postgres-backup).
#
# Раз в BACKUP_INTERVAL_SECONDS снимает pg_dump в custom-формате и удаляет копии
# старше BACKUP_KEEP_DAYS суток. Первая копия снимается сразу при старте.
#
# Источник — реплика, но только когда она получает поток: отставшая реплика
# отвечает и выглядит живой, а копия с неё — это данные на момент обрыва. Тогда
# копия снимается с основного сервера, а реплика сама становится unhealthy
# (её healthcheck проверяет поток) — проблему видно, а копии остаются свежими.
#
# Копия пишется во временный файл и переименовывается только после успеха:
# оборванный pg_dump не должен выглядеть готовой копией ни для восстановления,
# ни для healthcheck, который смотрит на возраст свежего *.dump.
#
# Подключение — переменными libpq PGUSER, PGPASSWORD, PGDATABASE; хосты —
# REPLICA_HOST и PRIMARY_HOST.
set -u

: "${BACKUP_INTERVAL_SECONDS:=86400}"
: "${BACKUP_KEEP_DAYS:=7}"
dir=/backups

source_host() {
    status="$(psql -h "$REPLICA_HOST" -XtA -c 'select status from pg_stat_wal_receiver' 2>/dev/null)"
    if [ "$status" = streaming ]; then
        echo "$REPLICA_HOST"
    else
        echo "backup: реплика не получает поток (статус walreceiver: ${status:-нет}) — копия с основного сервера" >&2
        echo "$PRIMARY_HOST"
    fi
}

while true; do
    # Хвосты оборванных копий (контейнер убит посреди pg_dump).
    find "$dir" -name '*.partial' -delete
    stamp="$(date -u +%Y%m%dT%H%M%SZ)"
    target="$dir/${PGDATABASE}-${stamp}.dump"
    host="$(source_host)"
    if pg_dump -h "$host" --format=custom --file="$target.partial"; then
        mv "$target.partial" "$target"
        echo "backup: готово $(basename "$target") с $host ($(du -h "$target" | cut -f1))"
        # -mmin, а не -mtime +N: тот округляет возраст вниз до суток и хранит
        # на одну копию больше, чем обещано.
        find "$dir" -name '*.dump' -mmin +"$((BACKUP_KEEP_DAYS * 1440))" -print -delete
    else
        # Минута, а не сутки: на старте стека база может ещё подниматься.
        echo "backup: pg_dump завершился ошибкой, повтор через минуту" >&2
        rm -f "$target.partial"
        sleep 60
        continue
    fi
    sleep "$BACKUP_INTERVAL_SECONDS"
done
