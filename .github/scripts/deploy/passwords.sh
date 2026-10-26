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
# Стоит после проверенного плана и pull образа и до миграций: они и новый API
# входят уже новым паролем, а pull в окно не входит. Пока полный apply не
# заменил API, воркер и копии, их открытые
# соединения живут, а новые со старым паролем отклоняются — это окно смены
# (infra/README.md, «Смена паролей»).
#
# Пароль не попадает ни в аргументы (их видно в `ps` на сервере и в логе ssh),
# ни в вывод: он идёт первой строкой stdin `docker exec -i`, его читает `read`
# оболочки в контейнере, а дальше — переменная окружения `psql` и stdin
# `rabbitmqctl` и `psql` через встроенный `printf`. Открытым текстом на сервер
# PostgreSQL он не уходит вовсе: `\password` psql без tty читает его из stdin
# и шлёт только SCRAM-хеш — в лог запросов, в выборку медленных и в
# `pg_stat_activity.query` (её видит `pg_monitor`, #130) попадает хеш.
# Тексты, которые уходят в `sh -c`, секретов не содержат.
#
# Пароль — только символы, безопасные в URL без кодирования (`openssl rand
# -hex 24`). Другие ломаются молча: `urlencode` в OpenTofu кодирует пробел как
# `+`, а SQLAlchemy и pika читают `+` буквально; `rabbitmqctl` обрезает пробелы
# по краям строки stdin — и при смене, и при проверке входа, так что шаг был
# бы зелёным, а API не вошёл бы. Такой секрет шаг отклоняет до сервера.
#
# Нужны: DOCKER_HOST — ssh://stand (хост из ~/.ssh/config,
# .github/actions/stand-ssh); PG_PASSWORD и RABBITMQ_PASSWORD — секреты
# PG_PASSWORD_DMC268_T1 и RABBITMQ_PASSWORD_DMC268_T1.
set -euo pipefail

: "${DOCKER_HOST:?нужен адрес демона Docker на сервере}"

for name in PG_PASSWORD RABBITMQ_PASSWORD; do
  if ! [[ "${!name:-}" =~ ^[A-Za-z0-9._~-]+$ ]]; then
    echo "::error::$name пуст или с символами вне A-Z a-z 0-9 . _ ~ -: такой пароль не дойдёт до URL подключения как есть. Новый — openssl rand -hex 24; пароли не меняю"
    exit 1
  fi
done

# Скрипт для `sh` контейнера postgres. Роль и база — из его окружения:
# POSTGRES_USER — роль, которую завёл initdb и под которой входят приложение и
# миграции. `\password` спрашивает пароль дважды; без tty — из stdin.
# ON_ERROR_STOP — чтобы сбой команды psql стал кодом возврата, а не строкой в
# выводе; повторный вход ловит и то, что код не покажет.
# shellcheck disable=SC2016 # переменные раскрывает sh контейнера
postgres_sh='set -eu
IFS= read -r NEW_PASSWORD
signs_in() {
  PGPASSWORD="$NEW_PASSWORD" psql -X -q -h dmc268-postgres -U "$POSTGRES_USER" -d "$POSTGRES_DB" \
    -tAc "select 1" < /dev/null > /dev/null 2>&1
}
if signs_in; then
  echo "postgres: пароль роли $POSTGRES_USER совпадает с секретом"
  exit 0
fi
printf "%s %s\n%s\n%s\n" "\\password" "$POSTGRES_USER" "$NEW_PASSWORD" "$NEW_PASSWORD" \
  | psql -X -q -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB" > /dev/null
if ! signs_in; then
  echo "::error::postgres: роль $POSTGRES_USER не входит новым паролем и после смены"
  exit 1
fi
echo "postgres: пароль роли $POSTGRES_USER сменён"'

printf '%s\n' "$PG_PASSWORD" | docker exec -i dmc268-postgres sh -c "$postgres_sh"

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
