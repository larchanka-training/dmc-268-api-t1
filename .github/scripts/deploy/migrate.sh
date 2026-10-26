#!/usr/bin/env bash
# Миграции схемы шагом деплоя: после того как `tofu apply -target` поднял базу,
# и до полного `tofu apply`, который выкатывает новый API и воркер (спека
# backend-delivery, «Миграции применяются до приёма трафика»). Упала миграция —
# шаг падает, apply не запускается, на стенде остаётся прежняя версия.
#
# Пока идут миграции, работает старый код: миграция обязана быть с ним
# совместима (expand/contract — скилл .agents/skills/alembic-migration).
#
# Одноразовый `docker run --rm` образа, который сейчас выкатится (тот же
# digest), на сервере, в сети стека: базу он находит по имени контейнера.
# Окружение — выход `migrate_env` (infra/outputs.tf), то есть то же
# local.app_env, что у api и worker. Оно идёт через env-файл с правами 600,
# который удаляется на выходе: секреты не попадают ни в аргументы команды,
# ни в лог.
#
# Ждать базу не нужно: у postgres `wait = true`, и targeted apply возвращается,
# когда она уже отвечает.
#
# Ждать миграция не может бесконечно: lock_timeout 5 с и statement_timeout
# 2 мин ставит сессии alembic/env.py, у шага — timeout-minutes: 10. Упала —
# транзакция откатывается, база остаётся на прежней ревизии (кроме DDL из
# autocommit_block). Повтор — только после того, как миграция не дождалась
# блокировки (длинная транзакция воркера или API), и только если база там же,
# откуда попытка начала: всего 3 попытки с паузой 15 с, и повтор не
# начинается позже 5 мин от старта шага — до потолка остаются 5 мин на
# попытку и вопрос к базе (ревью #220). Решает scripts/deploy_report.py
# `migrate-retry`.
#
# Нужны:
#   API_IMAGE    — ссылка на образ с digest (выход job'а image);
#   DOCKER_HOST  — ssh://stand (хост из ~/.ssh/config, .github/actions/stand-ssh);
#   `docker login ghcr.io` на раннере: docker CLI передаёт учётные данные
#   удалённому демону вместе с запросом pull — образ лежит в приватном пакете;
#   инициализированный infra/ и учётные данные хранилища состояния:
#   `tofu output` читает состояние через туннель (.github/actions/s3-backend).
# Параметры (env): REPORT_DIR — каталог отчёта деплоя, по умолчанию
# $RUNNER_TEMP/deploy-report: туда копируется вывод alembic, по строкам
# `Running upgrade A -> B` summary считает применённые ревизии
# (scripts/deploy_report.py), а после сбоя — вывод `alembic current`, и
# номер последней попытки (migrate-attempts). MIGRATE_RETRY_PAUSE — пауза
# перед повтором, с (15); MIGRATE_RETRY_DEADLINE — позже какой секунды шага
# повтор не начинается (300).
set -euo pipefail
umask 077

: "${API_IMAGE:?нужна ссылка на образ API}"
: "${DOCKER_HOST:?нужен адрес демона Docker на сервере}"
report_dir="${REPORT_DIR:-${RUNNER_TEMP:-/tmp}/deploy-report}"
mkdir -p "$report_dir"
attempts=3
pause="${MIGRATE_RETRY_PAUSE:-15}"
deadline="${MIGRATE_RETRY_DEADLINE:-300}"

env_file="$(mktemp)"
trap 'rm -f "$env_file"; docker rm -f dmc268-migrations >/dev/null 2>&1 || true' EXIT
tofu -chdir=infra output -raw migrate_env > "$env_file"

# Контейнер migrate из стека до #159: `removed` в infra/app.tf снял его с учёта,
# не уничтожая, и на сервере он остался остановленным со старым кодом выхода
# и логами прошлых миграций — их легко принять за текущие. Держит он и
# прежний образ, который иначе убрала бы чистка образов. Нужен один раз, на
# первом деплое после #159; дальше это no-op.
docker rm -f dmc268-migrate >/dev/null 2>&1 || true

# Этот pull — единственный для образа API: контейнеры api и worker полный apply
# создаёт из уже скачанного образа по той же ссылке (infra/app.tf).
docker pull --quiet "$API_IMAGE"

# Остаток прошлого запуска: раннер, убивший шаг по таймауту, убивает только
# docker CLI, а контейнер на сервере живёт дальше — и следующий деплой падал
# на занятом имени (#213). Параллельного запуска быть не может (concurrency
# `deploy`), поэтому живой контейнер с этим именем — сирота: его удаление
# рвёт соединение, и PostgreSQL откатывает его транзакцию. Тот же rm — в
# trap на выходе: прерванный сигналом шаг уберёт контейнер сам, если bash
# успеет выполнить trap.
docker rm -f dmc268-migrations >/dev/null 2>&1 || true

# Общие аргументы обоих запусков — upgrade и `alembic current` после сбоя:
# сеть и ограничения задаются здесь одни (ревью #220 — копия у второго
# запуска после #102 осталась в сети `dmc268`, где базы нет). Ограничения —
# как у api и worker в infra/app.tf (сверяет tests/test_stack_descriptions.py).
# Сеть — данных (dmc268-backend): база только в ней, и выхода наружу у
# миграций нет (сверяет tests/test_infra_isolation.py — у каждого запуска).
# shellcheck disable=SC2054 # запятые — часть значения --tmpfs, а не разделитель
migrations_run=(
  docker run --rm
  --network dmc268-backend
  --env-file "$env_file"
  --read-only
  --tmpfs /tmp:rw,noexec,nosuid,size=64m
  --security-opt no-new-privileges:true
  --cap-drop ALL
  --init
  --memory 256m
  --memory-swap 256m
)

# Ревизия базы после сбоя. Что откатилось, по логу не сказать:
# autocommit_block (0003) фиксирует всё до себя, а при оборванном ssh сирота
# мог довести миграции до commit, и код выхода тогда потерян (ревью #220).
# Ревизию summary и решение о повторе берут отсюда — `alembic current` тем же
# образом и окружением; сначала убирается сирота, чтобы он не дописал
# миграции после ответа. Не ответила база или ssh за 60 с — код в
# migrate-current.error, и summary пишет «ревизия неизвестна». Файлы прошлой
# попытки убираются: ответ — всегда о последней.
ask_current() {
  local code=0
  docker rm -f dmc268-migrations >/dev/null 2>&1 || true
  rm -f "$report_dir/migrate-current.log" "$report_dir/migrate-current.error"
  timeout 60 "${migrations_run[@]}" "$API_IMAGE" alembic current > "$report_dir/migrate-current.log" || code=$?
  [ "$code" -eq 0 ] || echo "$code" > "$report_dir/migrate-current.error"
}

# Логи alembic идут в stderr: 2>&1, чтобы попасть и в лог шага, и в файл;
# упавшую миграцию pipefail не прячет за tee. `-e` на время запуска снят:
# после сбоя нужен ещё вопрос к базе, а код выхода шага — код upgrade.
# migrate.log — вывод последней попытки: прежние откатились целиком.
attempt=1
while :; do
  echo "$attempt" > "$report_dir/migrate-attempts"
  set +e
  "${migrations_run[@]}" --name dmc268-migrations \
    "$API_IMAGE" alembic upgrade head 2>&1 | tee "$report_dir/migrate.log"
  status=$?
  set -e
  [ "$status" -eq 0 ] && exit 0
  ask_current
  if [ "$attempt" -lt "$attempts" ] && [ $((SECONDS + pause)) -lt "$deadline" ] \
    && REPORT_DIR="$report_dir" python3 scripts/deploy_report.py migrate-retry; then
    echo "::warning::миграции не дождались блокировки, база на прежней ревизии: попытка $((attempt + 1)) из $attempts через $pause с"
    sleep "$pause"
    attempt=$((attempt + 1))
    continue
  fi
  exit "$status"
done
