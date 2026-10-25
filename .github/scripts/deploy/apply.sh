#!/usr/bin/env bash
# Полный выкат: `tofu apply` плана, который построил и проверил plan.sh, и
# сырьё для разбивки простоя API в summary (scripts/deploy_report.py).
#
# Простой считается по часам сервера, а не раннера:
# - id контейнера API до apply — чтобы отличить старый от нового и понять,
#   пересоздавался ли он вообще;
# - события Docker по контейнеру `dmc268-api` все время apply: SIGTERM и выход
#   старого, проверки здоровья нового (`exec_die` с `exitCode`) и момент, когда
#   он стал healthy (`health_status: healthy`). Поток, а не `docker events
#   --since` после apply: события демон держит в буфере из 256 последних, а
#   каждая проверка здоровья любого контейнера — три события; пока воркер
#   дорабатывает сообщение (до 41 мин), остановку API из буфера вытеснили бы.
#   `--since` с запасом в минуту — на расхождение часов и на время, пока поток
#   подключается;
# - `docker inspect` нового сразу после apply: Created, StartedAt и журнал
#   проверок здоровья — запасной путь, если события healthy в потоке нет.
#   Журнал держит пять последних проверок, после healthy — раз в 30 с: если
#   apply шёл дольше пары минут после healthy (воркер дорабатывал сообщение),
#   первая успешная из него уже вытеснена. Берутся только Id, Created и State:
#   в полном inspect есть окружение контейнера с паролями.
#
# Вопросы серверу — под `timeout 30`: `ConnectTimeout` через живой
# ControlMaster не действует (TCP-соединения нет, клиент идёт в сокет), и
# пропавший без разрыва master держал бы `finish` до таймаута job'а. Не
# удалось спросить (ssh 255, timeout 124) — рядом с ответом файл `<имя>.error`
# с кодом: summary отличает «контейнера нет» от «сервер не ответил».
#
# Сбор сырья выкат не роняет: код возврата — код `tofu apply`. Файл плана
# удаляется на выходе в любом случае.
#
# Нужны: хост `stand` в ~/.ssh/config (.github/actions/stand-ssh), infra/tfplan
# от plan.sh и учётные данные хранилища состояния. Параметры (env):
# REPORT_DIR — каталог отчёта, по умолчанию $RUNNER_TEMP/deploy-report.
set -euo pipefail

dir="${REPORT_DIR:-${RUNNER_TEMP:-/tmp}/deploy-report}"
mkdir -p "$dir"

# Ответ сервера на команду — в файл отчёта; код ssh или timeout — в <файл>.error.
ask() {
  local file="$1" status=0
  shift
  timeout 30 ssh stand "$@" > "$dir/$file" 2>/dev/null || status=$?
  if [ "$status" -eq 124 ] || [ "$status" -eq 255 ]; then
    echo "$status" > "$dir/$file.error"
  fi
}

ask api-old-id "docker inspect -f '{{.Id}}' dmc268-api 2>/dev/null"

since="$(( $(date +%s) - 60 ))"
# shellcheck disable=SC2029 # since — время раннера, раскрывается здесь намеренно
ssh stand "docker events --since $since --filter type=container --filter container=dmc268-api --format '{{json .}}'" \
  > "$dir/api-events.jsonl" 2>/dev/null &
watcher=$!

finish() {
  rm -f infra/tfplan
  ask api-new.json "docker inspect -f '{\"Id\":{{json .Id}},\"Created\":{{json .Created}},\"State\":{{json .State}}}' dmc268-api"
  kill "$watcher" 2>/dev/null || true
  wait "$watcher" 2>/dev/null || true
}
trap finish EXIT

tofu -chdir=infra apply -input=false -no-color tfplan
