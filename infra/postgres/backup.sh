#!/bin/sh
# Резервные копии базы (dmc268-postgres-backup).
#
# Раз в BACKUP_INTERVAL_SECONDS снимает pg_dump в custom-формате с реплики —
# не нагружая основной сервер — и удаляет копии старше BACKUP_KEEP_DAYS.
# Первая копия снимается сразу при старте контейнера.
#
# Копия пишется во временный файл и переименовывается только после успеха:
# оборванный pg_dump не должен выглядеть готовой копией ни для восстановления,
# ни для healthcheck, который смотрит на возраст свежего *.dump.
#
# Подключение — переменными libpq: PGHOST, PGUSER, PGPASSWORD, PGDATABASE.
set -u

: "${BACKUP_INTERVAL_SECONDS:=86400}"
: "${BACKUP_KEEP_DAYS:=7}"
dir=/backups

while true; do
    stamp="$(date -u +%Y%m%dT%H%M%SZ)"
    target="$dir/${PGDATABASE}-${stamp}.dump"
    if pg_dump --format=custom --file="$target.partial"; then
        mv "$target.partial" "$target"
        echo "backup: готово $(basename "$target") ($(du -h "$target" | cut -f1))"
        find "$dir" -name '*.dump' -mtime +"$BACKUP_KEEP_DAYS" -print -delete
    else
        # Минута, а не сутки: на старте стека реплика может ещё собираться.
        echo "backup: pg_dump завершился ошибкой, повтор через минуту" >&2
        rm -f "$target.partial"
        sleep 60
        continue
    fi
    sleep "$BACKUP_INTERVAL_SECONDS"
done
