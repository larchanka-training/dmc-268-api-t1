#!/usr/bin/env bash
# Туннель к хранилищу состояния OpenTofu и сторож при нём. Открывает
# .github/actions/s3-backend и заново — восстановление состояния после обрыва
# (.github/scripts/deploy/recover-state.sh): поэтому прежний туннель и его
# сторож сначала закрываются, иначе новый упал бы на занятом порте.
#
# Туннель — своё соединение, мимо ControlMaster из stand-ssh
# (ControlPath=none): через master проброс открывал бы master,
# ExitOnForwardFailure не срабатывал бы, а туннель закрылся бы через
# ControlPersist простоя посреди деплоя. ExitOnForwardFailure обязателен: без
# него ssh уходит в фон и при неудавшемся пробросе.
#
# ServerAliveCountMax=12 (две минуты при ServerAliveInterval 10 из
# ~/.ssh/config) вместо трёх у хоста stand. Обрыв туннеля посреди записи
# состояния — самый дорогой сбой деплоя: состояние остаётся на раннере, а
# блокировка в хранилище. Пока туннель жив, запрос OpenTofu ждёт в нём и
# доходит, когда связь вернулась; закрылся туннель — запрос падает. Поэтому
# туннель терпит провал связи дольше, чем master: тот рвётся за 30 с, и
# docker-провайдер падает, а последняя запись состояния после этого ещё
# успевает пройти, если связь вернулась в пределах двух минут.
#
# Сторож (s3-watchdog.sh) закрывает туннель, если хранилище за ним перестало
# отвечать, а соединение живо: таймаута запроса у s3-бэкенда OpenTofu нет, и
# без сторожа запись состояния висела бы до таймаута job'а.
#
# Нужен хост `stand` в ~/.ssh/config (.github/actions/stand-ssh). Аргумент —
# порт хранилища, он же локальный конец туннеля. Параметры (env): REPORT_DIR —
# каталог отчёта деплоя (по умолчанию $RUNNER_TEMP/deploy-report), туда пишет
# сторож; S3_TUNNEL_WAIT — сколько секунд ждать ответа хранилища (30;
# recover-state.sh ждёт дольше — хранилище могло зависнуть).
set -euo pipefail

port="${1:?нужен порт хранилища}"
wait_for="${S3_TUNNEL_WAIT:-30}"
here="$(cd "$(dirname "$0")" && pwd)"
report_dir="${REPORT_DIR:-${RUNNER_TEMP:-/tmp}/deploy-report}"
mkdir -p "$report_dir"

# Шаблон с ^ и $: под него не попадает shell, в аргументах которого он есть.
forward="${port}:127.0.0.1:${port}"
pkill -f "^bash .*s3-watchdog\.sh ${port}\$" 2>/dev/null || true
pkill -f "^ssh .* -L ${forward//./\\.} stand\$" 2>/dev/null || true
for _ in $(seq 1 20); do
  (exec 3<>"/dev/tcp/127.0.0.1/${port}") 2>/dev/null || break
  sleep 0.25
done

# Одной строкой: tests/test_stand_ssh.py ищет `ssh … -L` без ControlPath=none
# построчно.
ssh -o ControlPath=none -o ServerAliveCountMax=12 -o ExitOnForwardFailure=yes -f -N -L "$forward" stand

# Ждём ответа хранилища, а не открытого порта: порт на петле слушает сам ssh,
# и он открыт, даже когда хранилище на сервере лежит, — ошибка всплыла бы
# позже, на `tofu init`, без подсказки (#211). Любой код HTTP, даже 403, —
# хранилище живо; 000 — ответа нет. Так же — state-tunnel во фронте.
started="$(date +%s)"
while true; do
  code="$(curl -s -o /dev/null -w '%{http_code}' --max-time 3 "http://127.0.0.1:${port}/" || true)"
  if [ -n "$code" ] && [ "$code" != "000" ]; then
    echo "туннель открыт, хранилище отвечает (HTTP ${code})"
    # Сторож живёт до конца job'а; вывод — в файл: лог шага закрывается
    # вместе с шагом.
    nohup bash "$here/s3-watchdog.sh" "$port" >> "$report_dir/s3-watchdog.log" 2>&1 < /dev/null &
    exit 0
  fi
  [ $(( $(date +%s) - started )) -lt "$wait_for" ] || break
  sleep 1
done
echo "::error::туннель поднялся, но хранилище за ним не ответило за ${wait_for} с: проверьте контейнер dmc268-s3 на сервере"
exit 1
