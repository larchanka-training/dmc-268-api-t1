#!/usr/bin/env bash
# Получает ли горячая реплика поток после выката (спека backend-delivery,
# «Горячая реплика базы данных», «Реплика восстанавливается сама»).
#
# Реплика, которая запущена, но не получает поток, внешне неотличима от
# рабочей: контейнер жив, на запросы отвечает. Поэтому проверяем со стороны
# основного сервера — слот dmc268_replica активен, и держит его именно реплика
# (application_name dmc268_replica), а не pg_basebackup первой сборки: у того
# своё имя, dmc268_basebackup, и свой walsender в том же состоянии.
#
# Реплика, потерявшая поток между деплоями (слот потерян по
# max_slot_wal_keep_size), — unhealthy, но Docker такие контейнеры не
# перезапускает. Перезапуск даёт entrypoint пересобрать её; на другом кластере
# основного сервера она остановится, ничего не стерев (infra/README.md).
#
# Первая сборка на пустом томе копирует базу целиком — на всё до пяти минут.
# Пользователь и база — из окружения контейнера, запрос — через stdin: в
# командной строке нет ни кавычек запроса, ни захардкоженных имён.
#
# Не дождались — печатает хвосты логов реплики и настройки роли и падает.
# `-e` нет намеренно: пустой ответ (ssh упал, база перезапускается) — это
# очередной опрос, а не конец проверки.
#
# Нужен хост `stand` в ~/.ssh/config (.github/actions/stand-ssh). Параметров нет.
set -uo pipefail

query="select r.state from pg_stat_replication r join pg_replication_slots s on s.active_pid = r.pid where s.slot_name = 'dmc268_replica' and r.application_name = 'dmc268_replica'"
remote="docker exec -i dmc268-postgres sh -c 'psql -U \"\$POSTGRES_USER\" -d \"\$POSTGRES_DB\" -XtA'"

health="$(ssh -o ConnectTimeout=5 stand \
  "docker inspect -f '{{.State.Health.Status}}' dmc268-postgres-replica" 2>/dev/null)"
if [ "$health" = "unhealthy" ]; then
  echo "::warning::реплика unhealthy до проверки — перезапускаю"
  ssh -o ConnectTimeout=5 stand "docker restart dmc268-postgres-replica" >/dev/null
fi

state=""
deadline=$(( $(date +%s) + 300 ))
while [ "$(date +%s)" -lt "$deadline" ]; do
  state="$(printf '%s\n' "$query" \
    | ssh -o ConnectTimeout=5 stand "$remote" 2>/dev/null \
    | tr -d '[:space:]')"
  if [ "$state" = "streaming" ]; then
    echo "реплика получает поток"
    exit 0
  fi
  sleep 5
done

echo "::error::реплика не перешла в streaming за пять минут (состояние: '${state:-нет соединения}')"
ssh -o ConnectTimeout=5 stand \
  "docker logs --tail 50 dmc268-postgres-replica 2>&1; echo '--- настройка репликации ---'; docker logs --tail 30 dmc268-replication-setup 2>&1" || true
exit 1
