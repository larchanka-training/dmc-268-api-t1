#!/usr/bin/env bash
# Доходят ли вебхуки GitHub до стенда — по логам контейнеров, которые сейчас
# заменит выкат: у новых контейнеров логи пустые, поэтому шаг стоит до
# применения. Доставка снаружи сначала попадает в nginx интерфейса
# (`dmc268-ui`): на чужой путь он ответит сам, и API её не увидит, — поэтому
# запросы GitHub (User-Agent `GitHub-Hookshot`) считаются и там. `dmc268-ui`
# выкатывает репозиторий интерфейса, не этот деплой: его лог — со своего
# старта, время старта печатается рядом.
#
# Только числа и пути без query: лог Actions видят все, кто читает
# репозиторий, — строк запросов и текстов ошибок здесь нет. Нет доставок —
# смотреть GitHub App, `401` — секрет вебхука, `202` при пустом стенде —
# воркер.
#
# Диагностика, а не выкат: шаг в deploy.yml не роняет прогон
# (continue-on-error), а `-e` здесь нет намеренно — пустой grep не должен
# обрывать остальные счётчики.
#
# Нужен хост `stand` в ~/.ssh/config (.github/actions/stand-ssh). Параметров нет.
set -uo pipefail

ssh stand sh -s <<'EOF'
echo "Доставки POST /api/v1/webhooks/github по кодам ответа (пусто — доставок не было):"
docker logs dmc268-api 2>&1 | grep -F 'POST /api/v1/webhooks/github' \
  | grep -oE '" [0-9]{3}' | tr -d '" ' | sort | uniq -c
echo "Всего запросов в access-логе API (ноль — лог запросов не пишется): $(docker logs dmc268-api 2>&1 | grep -cE '" [0-9]{3}')"
echo "Запросы GitHub (User-Agent GitHub-Hookshot) к nginx — метод, путь без query, код (пусто — с момента старта dmc268-ui, см. ниже, запросов GitHub не было):"
docker logs dmc268-ui 2>&1 | grep -F 'GitHub-Hookshot' \
  | awk '{ sub(/\?.*/, "", $7); print $6, $7, $9 }' | tr -d '"' | sort | uniq -c
echo "Строк воркера с ERROR или Traceback: $(docker logs dmc268-worker 2>&1 | grep -cE 'ERROR|Traceback')"
echo "Контейнеры живут с: $(docker inspect -f '{{.Name}} {{.State.StartedAt}}' dmc268-api dmc268-worker dmc268-ui 2>/dev/null | tr '\n' ' ')"
EOF
