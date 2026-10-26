#!/usr/bin/env bash
# Пароли базы и брокера — из текущих секретов деплоя (#161, спека
# backend-delivery, «Смена пароля базы и брокера — обычное развёртывание»).
#
# POSTGRES_PASSWORD и RABBITMQ_DEFAULT_PASS образы читают только на пустом
# томе: новый секрет дошёл бы до API, воркера и миграций, но не до базы и
# брокера, и они не вошли бы. Поэтому пароль выставляется здесь, изнутри
# контейнеров: у postgres — `psql` по локальному сокету (`trust` в
# infra/postgres/pg_hba.conf, старый пароль не нужен), у брокера —
# `rabbitmqctl change_password`. Сами контейнеры смену секрета не видят
# (`ignore_changes = [env]` в infra/postgres.tf и infra/rabbitmq.tf) и не
# пересоздаются.
#
# Шаг идемпотентный: сначала вход новым паролем — у postgres по сети, как
# входят API и миграции (`samenet`, scram), у брокера — `authenticate_user`. Вошёл
# — ничего не меняется. Нет — пароль меняется и вход проверяется снова: не
# вошёл и теперь — шаг падает, и деплой не идёт дальше.
#
# Стоит после проверенного плана и до миграций: они и новый API входят уже
# новым паролем. Пока полный apply не заменил API, воркер и копии, их открытые
# соединения живут, а новые со старым паролем отклоняются — это окно смены
# (infra/README.md, «Смена паролей»).
#
# Пароль не попадает ни в аргументы (их видно в `ps` на сервере и в логе ssh),
# ни в вывод: он идёт первой строкой stdin `docker exec -i`, его читает `read`
# оболочки в контейнере, а дальше — переменная окружения `psql` и stdin
# `rabbitmqctl`. Упавший ALTER ROLE PostgreSQL записал бы в свой лог строкой
# STATEMENT, а psql вывел бы строкой LINE — обе с паролем (проверено на
# pgvector/pgvector:pg18): сессия отключает и то и другое. Тексты, которые
# уходят в `sh -c`, секретов не содержат.
#
# Нужны: DOCKER_HOST — ssh://stand (хост из ~/.ssh/config,
# .github/actions/stand-ssh); PG_PASSWORD и RABBITMQ_PASSWORD — секреты
# PG_PASSWORD_DMC268_T1 и RABBITMQ_PASSWORD_DMC268_T1, в одну строку.
set -euo pipefail

: "${DOCKER_HOST:?нужен адрес демона Docker на сервере}"

for name in PG_PASSWORD RABBITMQ_PASSWORD; do
  value="${!name:-}"
  if [ -z "$value" ]; then
    echo "::error::$name пуст: секрет не задан, пароль не меняю"
    exit 1
  fi
  # Пароль — первая строка stdin: перевод строки разрезал бы его.
  case "$value" in
    *$'\n'* | *$'\r'*)
      echo "::error::$name содержит перевод строки: так пароль не передать"
      exit 1
      ;;
  esac
done

# Скрипт для `sh` контейнера postgres. Роль и база — из его окружения:
# POSTGRES_USER — роль, которую завёл initdb и под которой входят приложение и
# миграции. Остаток stdin после пароля — SQL для psql.
# shellcheck disable=SC2016 # переменные раскрывает sh контейнера
postgres_sh='set -eu
IFS= read -r NEW_PASSWORD
export NEW_PASSWORD
signs_in() {
  PGPASSWORD="$NEW_PASSWORD" psql -X -q -h dmc268-postgres -U "$POSTGRES_USER" -d "$POSTGRES_DB" \
    -tAc "select 1" < /dev/null > /dev/null 2>&1
}
if signs_in; then
  echo "postgres: пароль роли $POSTGRES_USER совпадает с секретом"
  exit 0
fi
psql -X -q -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB"
if ! signs_in; then
  echo "::error::postgres: роль $POSTGRES_USER не входит новым паролем и после смены"
  exit 1
fi
echo "postgres: пароль роли $POSTGRES_USER сменён"'

# \getenv, а не -v: значение в аргументах psql было бы видно в `ps`.
# track_utility — на случай pg_stat_statements: без него текст ALTER ROLE с
# паролем лёг бы в статистику запросов; без расширения SET безвреден.
postgres_sql='\set VERBOSITY terse
\set SHOW_CONTEXT never
\getenv role POSTGRES_USER
\getenv new_password NEW_PASSWORD
SET log_statement = none;
SET log_min_error_statement = panic;
SET pg_stat_statements.track_utility = off;
ALTER ROLE :"role" PASSWORD :'"'"'new_password'"'"';'

{
  printf '%s\n' "$PG_PASSWORD"
  printf '%s\n' "$postgres_sql"
} | docker exec -i dmc268-postgres sh -c "$postgres_sh"

# Скрипт для `sh` контейнера брокера. Пользователь — тот, кого брокер завёл из
# RABBITMQ_DEFAULT_USER на пустом томе. На чистом сервере брокер только что
# создан (`wait` у его контейнера нет): ждём старта узла до двух минут.
# shellcheck disable=SC2016 # переменные раскрывает sh контейнера
rabbitmq_sh='set -eu
IFS= read -r NEW_PASSWORD
user="$RABBITMQ_DEFAULT_USER"
tries=0
until rabbitmqctl -q await_startup > /dev/null 2>&1; do
  tries=$((tries + 1))
  if [ "$tries" -ge 60 ]; then
    echo "::error::rabbitmq: узел не запустился за две минуты"
    exit 1
  fi
  sleep 2
done
signs_in() { printf "%s\n" "$NEW_PASSWORD" | rabbitmqctl -q authenticate_user "$user" > /dev/null 2>&1; }
if signs_in; then
  echo "rabbitmq: пароль пользователя $user совпадает с секретом"
  exit 0
fi
printf "%s\n" "$NEW_PASSWORD" | rabbitmqctl -q change_password "$user" > /dev/null
if ! signs_in; then
  echo "::error::rabbitmq: пользователь $user не входит новым паролем и после смены"
  exit 1
fi
echo "rabbitmq: пароль пользователя $user сменён"'

printf '%s\n' "$RABBITMQ_PASSWORD" | docker exec -i dmc268-rabbitmq sh -c "$rabbitmq_sh"
