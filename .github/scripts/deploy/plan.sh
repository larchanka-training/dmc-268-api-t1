#!/usr/bin/env bash
# План полного выката в файл и сторож томов до apply (спека backend-delivery,
# «Развёртывание не уничтожает тома данных»).
#
# `tofu plan -out` пишет план в infra/tfplan, `tofu show -json` отдаёт его
# scripts/deploy_report.py: тот считает изменения по типам ресурсов для
# summary и падает, если план уничтожает или заменяет том базы или брокера.
# Тогда apply не запускается: применяется ровно этот план (apply.sh), другого
# не строится.
#
# В плане и в его JSON значения переменных — пароли и ключи — лежат открытым
# текстом. JSON идёт только трубой, на диск не пишется; файл плана не
# загружается артефактом, а удаляется: apply.sh — после применения, этот
# скрипт — если план отклонён или не построился.
#
# Нужны: инициализированный infra/ (шаг «Поднять базу и брокер»), учётные
# данные хранилища состояния и TF_VAR_* — те же, что у apply (якорь
# *tofu-env в deploy.yml). Параметры (env): REPORT_DIR — каталог отчёта, по
# умолчанию $RUNNER_TEMP/deploy-report.
set -euo pipefail

export REPORT_DIR="${REPORT_DIR:-${RUNNER_TEMP:-/tmp}/deploy-report}"
drop_rejected_plan() {
  local status=$?
  [ "$status" -eq 0 ] || rm -f infra/tfplan
}
trap drop_rejected_plan EXIT

tofu -chdir=infra plan -input=false -no-color -out=tfplan
tofu -chdir=infra show -json tfplan | python3 scripts/deploy_report.py plan
