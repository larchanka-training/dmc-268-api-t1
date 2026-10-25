#!/usr/bin/env bash
# Жив ли API после выката: `/health` через SSH-туннель к петле сервера.
#
# Через туннель, а не по публичному адресу: так «сервис мёртв» не путается с
# «порт закрыт снаружи». Публичную доступность `/api/v1/` проверяет деплой
# интерфейса. `/health` по контракту отвечает независимо от базы, поэтому
# упавшие миграции ловит не он, а постусловие контейнера migrate в apply.
#
# Ждёт до минуты (30 попыток раз в 2 с). Не дождался — печатает хвосты логов
# API и миграций и падает.
#
# Нужен хост `stand` в ~/.ssh/config (.github/actions/stand-ssh). Параметров нет:
# локальный конец туннеля — 18000, API на сервере — 127.0.0.1:8000.
set -euo pipefail

# ExitOnForwardFailure: без него ssh уходит в фон и при неудавшемся пробросе.
ssh -o ExitOnForwardFailure=yes -f -N -L 18000:127.0.0.1:8000 stand

for _ in $(seq 1 30); do
  if curl -fsS --max-time 3 http://127.0.0.1:18000/health >/dev/null 2>&1; then
    echo "/health отвечает"
    exit 0
  fi
  sleep 2
done

echo "::error::/health не ответил за минуту"
ssh stand \
  "docker logs --tail 50 dmc268-api 2>&1; echo '--- миграции ---'; docker logs --tail 30 dmc268-migrate 2>&1" || true
exit 1
