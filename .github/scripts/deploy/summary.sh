#!/usr/bin/env bash
# Summary деплоя: исход, простой API, план, миграции, диск и шаги — из того,
# что сложили в каталог отчёта шаги job'а (scripts/deploy_report.py).
#
# Шаг идёт всегда (`if: always()`), в том числе после упавшего, и сам не падает:
# не собралось значение — в summary нет строки, а не красный прогон. Поэтому
# `-e` нет.
#
# Свободное место — здесь, а не после уборки отдельным шагом: summary идёт
# сразу за ней. Раздел Docker — каталог из `docker info`; на корневой файловой
# системе он сольётся с `/`.
#
# Нужен хост `stand` в ~/.ssh/config (.github/actions/stand-ssh). Параметры
# (env): REPORT_DIR — каталог отчёта (по умолчанию $RUNNER_TEMP/deploy-report),
# GITHUB_STEP_SUMMARY и для render — STEP_RESULTS, JOB_STATUS,
# IMAGE, SHA, HOST.
set -uo pipefail

export REPORT_DIR="${REPORT_DIR:-${RUNNER_TEMP:-/tmp}/deploy-report}"
mkdir -p "$REPORT_DIR"

# План полного выката строится до миграций: если они упали, apply не шёл и
# файл плана с паролями остался — убрать его на любом исходе.
rm -f infra/tfplan

# timeout 30: ConnectTimeout через живой ControlMaster не действует, и
# пропавший без разрыва master держал бы шаг до его timeout-minutes.
# shellcheck disable=SC2016 # переменные раскрывает shell сервера
timeout 30 ssh -o ConnectTimeout=10 stand \
  'root="$(docker info -f "{{.DockerRootDir}}")"; echo "docker_root $root"; df -Pk / "$root"' \
  > "$REPORT_DIR/disk.txt" 2>/dev/null || true

if ! python3 scripts/deploy_report.py render >> "${GITHUB_STEP_SUMMARY:?}"; then
  echo "::warning::summary деплоя не собрался"
fi
exit 0
