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
# Цикла повторов нет: у postgres `wait = true`, и targeted apply возвращается,
# когда база уже отвечает. Ошибка миграции видна с первой попытки.
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
# (scripts/deploy_report.py).
set -euo pipefail
umask 077

: "${API_IMAGE:?нужна ссылка на образ API}"
: "${DOCKER_HOST:?нужен адрес демона Docker на сервере}"
report_dir="${REPORT_DIR:-${RUNNER_TEMP:-/tmp}/deploy-report}"
mkdir -p "$report_dir"

env_file="$(mktemp)"
trap 'rm -f "$env_file"' EXIT
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

# Ограничения — как у api и worker в infra/app.tf (сверяет
# tests/test_stack_descriptions.py). Имя — чтобы второй запуск поверх
# зависшего первого упал на конфликте имён, а не мигрировал параллельно.
#
# После миграций — перегенерация пароля роли мониторинга dmc268_monitor
# (создаёт миграция 0013): ALTER ROLE идемпотентен, пароль сводится с
# POSTGRES_MONITOR_PASSWORD из выхода migrate_env (infra/outputs.tf) на каждом
# деплое, а старый пароль из прошлого секрета перестаёт действовать. Пароль —
# параметром запроса psycopg, не литералом команды: литерал осел бы в /proc и
# docker logs. track_utility выключается той же транзакцией (SET LOCAL живёт
# до COMMIT): иначе ALTER ROLE ... PASSWORD лёг бы в pg_stat_statements
# открытым текстом. Тот же шаг — у сервиса migrate в docker-compose.yml.
# Логи идут в stderr: 2>&1, чтобы попасть и в лог шага, и в файл отчёта;
# упавшую миграцию pipefail не прячет за tee.
docker run --rm --name dmc268-migrations \
  --network dmc268 \
  --env-file "$env_file" \
  --read-only \
  --tmpfs /tmp:rw,noexec,nosuid,size=64m \
  --security-opt no-new-privileges:true \
  --cap-drop ALL \
  --init \
  --memory 256m \
  --memory-swap 256m \
  "$API_IMAGE" sh -c 'alembic upgrade head && python - <<"PY"
import os

import psycopg

# DATABASE_URL настроек — под схемой SQLAlchemy (postgresql+psycopg://);
# psycopg ждёт стандартную схему postgresql://. Привязка значений — на
# клиенте (ClientCursor): серверная не проходит по грамматике ALTER ROLE
# (PASSWORD принимает только литерал, не $1), а литерал руками собирать
# нельзя — psycopg экранирует сам.
dsn = os.environ["DATABASE_URL"].replace("postgresql+psycopg://", "postgresql://", 1)
with psycopg.connect(dsn, cursor_factory=psycopg.ClientCursor) as conn:
    with conn.transaction():
        conn.execute("SET LOCAL pg_stat_statements.track_utility = off")
        # Имя роли зашито — то же, что в миграции 0013 и env коллектора.
        conn.execute(
            "ALTER ROLE dmc268_monitor PASSWORD %s",
            (os.environ["POSTGRES_MONITOR_PASSWORD"],),
        )
PY' 2>&1 | tee "$report_dir/migrate.log"
