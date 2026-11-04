#!/usr/bin/env bash
# Сеть, база и брокер до миграций: targeted-план в файл, сторож, apply ровно
# этого плана (спека backend-delivery, «Развёртывание не пересоздаёт базу и
# брокер»).
#
# На живом стенде план — `No changes`, на чистом сервере — создание сетей,
# томов, базы и брокера. Реплику и копии здесь не поднимаем: миграциям нужен
# только основной сервер, их создаёт полный apply. Замену или удаление контейнера базы или брокера
# (смена env, памяти, log_opts, образа) и тома scripts/deploy_report.py
# отклоняет: apply не запускается, стенд остаётся как был. Раньше шаг делал
# `tofu apply -auto-approve -target=…` и пересоздавал базу мимо всякой
# проверки (#213). Осознанный обход — ручной запуск
# `gh workflow run deploy.yml -f allow_stateful_replace=true`
# (ALLOW_STATEFUL_REPLACE в env): замена тогда — предупреждение; тома флаг не
# открывает. Смена пароля базы и брокера сюда не доходит: их env план не
# видит (ignore_changes), пароль выставляет следующий шаг — passwords.sh (#161).
#
# Предупреждение OpenTofu о -target здесь ожидаемое. У postgres `wait = true`
# (infra/postgres.tf): apply возвращается, когда база отвечает. Этот же apply
# обновляет в состоянии выход migrate_env — он зависит от этих контейнеров
# (infra/locals.tf).
#
# Файл плана с паролями внутри удаляется на выходе при любом исходе, JSON
# идёт только трубой — как в plan.sh.
#
# Нужны: учётные данные хранилища состояния и TF_VAR_* — те же, что у полного
# apply (якорь *tofu-env в deploy.yml). Параметры (env): REPORT_DIR — каталог
# отчёта, по умолчанию $RUNNER_TEMP/deploy-report; ALLOW_STATEFUL_REPLACE
# (`true` — разрешить замену базы и брокера) и DEPLOY_ACTOR — для сторожа.
set -euo pipefail

export REPORT_DIR="${REPORT_DIR:-${RUNNER_TEMP:-/tmp}/deploy-report}"
# Модель Eurorouter — одна на стенд и eval: файл .github/eurorouter-model, а
# не переменная в deploy.yml (#245). apply.sh применяет готовый план, ему не нужна.
TF_VAR_eurorouter_model="$(tr -d '[:space:]' < "${BASH_SOURCE[0]%/*}/../../eurorouter-model")"
export TF_VAR_eurorouter_model
# reasoning_effort — из соседнего файла: пустой — параметр не задан (#267).
TF_VAR_eurorouter_reasoning_effort="$(tr -d '[:space:]' < "${BASH_SOURCE[0]%/*}/../../eurorouter-reasoning-effort")"
export TF_VAR_eurorouter_reasoning_effort
trap 'rm -f infra/infra.tfplan' EXIT

# -lock-timeout: чужая короткая блокировка того же ключа (ручной plan) не роняет
# деплой сразу — он ждёт её до двух минут, как plan.sh и apply.sh (#215).
tofu -chdir=infra init -input=false -no-color
tofu -chdir=infra plan -input=false -no-color -lock-timeout=2m -out=infra.tfplan \
  -target=docker_network.dmc268 \
  -target=docker_network.backend \
  -target=docker_container.postgres \
  -target=docker_container.rabbitmq
tofu -chdir=infra show -json infra.tfplan | python3 scripts/deploy_report.py plan infra
tofu -chdir=infra apply -input=false -no-color -lock-timeout=2m infra.tfplan
