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
# открывает. Ротация пароля — runbook (#161), не деплой.
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
trap 'rm -f infra/infra.tfplan' EXIT

tofu -chdir=infra init -input=false -no-color
tofu -chdir=infra plan -input=false -no-color -out=infra.tfplan \
  -target=docker_network.dmc268 \
  -target=docker_network.backend \
  -target=docker_container.postgres \
  -target=docker_container.rabbitmq
tofu -chdir=infra show -json infra.tfplan | python3 scripts/deploy_report.py plan infra
tofu -chdir=infra apply -input=false -no-color infra.tfplan
