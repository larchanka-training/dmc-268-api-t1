#!/usr/bin/env bash
# Сторож туннеля к хранилищу состояния: закрывает туннель, если хранилище за
# ним перестало отвечать, а соединение живо.
#
# Таймаута запроса у s3-бэкенда OpenTofu 1.13.2 нет: ни параметра в backend
# "s3", ни таймаута у HTTP-клиента aws-sdk-go-base, на котором он построен, —
# только max_retries, и тот считает ответы с ошибкой, а не ожидание. Если
# хранилище зависло (в репетиции — `docker pause`), а SSH жив, ServerAlive
# туннеля не срабатывает, и запись состояния висит до таймаута job'а. Закрытый
# туннель превращает зависание в обрыв: висящий запрос получает разрыв,
# повторы — отказ в соединении, и OpenTofu пишет `errored.tfstate`, которое
# поднимает .github/scripts/deploy/recover-state.sh.
#
# Зависание — это таймаут запроса (curl 28). Отказ в соединении и разрыв —
# не зависание: OpenTofu падает на них сам, сторож их не трогает. Любой ответ
# HTTP, даже 403, — хранилище живо.
#
# Запускает s3-tunnel.sh в фоне до конца job'а; закрыв туннель, сторож
# выходит — новый туннель придёт со своим. Аргумент — порт туннеля.
# Параметры (env): S3_WATCHDOG_LIMIT — сколько секунд зависания терпеть
# (по умолчанию 120, как ServerAlive туннеля), S3_WATCHDOG_INTERVAL — пауза
# между проверками (10).
set -uo pipefail

port="${1:?нужен порт туннеля}"
limit="${S3_WATCHDOG_LIMIT:-120}"
interval="${S3_WATCHDOG_INTERVAL:-10}"
forward="${port}:127\\.0\\.0\\.1:${port}"

hung_since=""
while true; do
  rc=0
  curl -s -o /dev/null --max-time 10 "http://127.0.0.1:${port}/" || rc=$?
  now="$(date +%s)"
  if [ "$rc" -ne 28 ]; then
    hung_since=""
  else
    hung_since="${hung_since:-$now}"
    if [ $(( now - hung_since )) -ge "$limit" ]; then
      echo "$(date -u +%FT%TZ) хранилище не отвечает $(( now - hung_since )) с: туннель закрыт"
      pkill -f "^ssh .* -L ${forward} stand\$" || true
      exit 0
    fi
  fi
  sleep "$interval"
done
