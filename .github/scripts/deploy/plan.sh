#!/usr/bin/env bash
# План полного выката в файл и сторож томов до apply (спека backend-delivery,
# «Развёртывание не уничтожает тома данных»).
#
# `tofu plan -out` пишет план в infra/tfplan, `tofu show -json` отдаёт его
# scripts/deploy_report.py: тот считает изменения по типам ресурсов для
# summary и падает, если план уничтожает или заменяет том базы или брокера
# либо пересоздаёт их контейнер (тот же сторож — у targeted-плана в infra.sh;
# замену контейнеров разрешает ALLOW_STATEFUL_REPLACE=true ручного запуска).
# Тогда apply не запускается: применяется ровно этот план (apply.sh), другого
# не строится. Первым план с заменой тома отклоняет сам OpenTofu — у томов
# prevent_destroy (infra/postgres.tf); сторож томов здесь — для summary и на
# случай, если prevent_destroy снимут. Строится до миграций: если они упали, файл плана
# удаляет summary.sh.
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
# Модель Eurorouter — одна на стенд и eval: файл .github/eurorouter-model, а
# не переменная в deploy.yml (#245). apply.sh применяет готовый план, ему не нужна.
TF_VAR_eurorouter_model="$(tr -d '[:space:]' < "${BASH_SOURCE[0]%/*}/../../eurorouter-model")"
export TF_VAR_eurorouter_model
drop_rejected_plan() {
  local status=$?
  [ "$status" -eq 0 ] || rm -f infra/tfplan
}
trap drop_rejected_plan EXIT

# -lock-timeout: чужая короткая блокировка того же ключа (ручной plan) не роняет
# деплой сразу — он ждёт её до двух минут. Деплой интерфейса держит свой ключ
# и не мешает; очередь деплоев бэкенда держит concurrency в deploy.yml.
tofu -chdir=infra plan -input=false -no-color -lock-timeout=2m -out=tfplan
tofu -chdir=infra show -json tfplan | python3 scripts/deploy_report.py plan
