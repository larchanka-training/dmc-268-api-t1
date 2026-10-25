#!/usr/bin/env bash
# Засечка начала шага для таблицы шагов в summary деплоя: строка
# `<id шага> <время>` в файле marks каталога отчёта. Длительность шага — до
# следующей засечки (scripts/deploy_report.py), поэтому её ставит первой
# строкой каждый шаг с `run` в job'е deploy; шаги с `uses` засечку поставить
# не могут и входят в предыдущий. Id шага — GITHUB_ACTION: у шага с `id` это
# его id.
#
# Засечка — для отчёта, а не для выката: не записалась — предупреждение, но
# шаг не падает. Поэтому `-e` нет.
#
# Параметры (env): REPORT_DIR — каталог отчёта, по умолчанию
# $RUNNER_TEMP/deploy-report.
set -uo pipefail

dir="${REPORT_DIR:-${RUNNER_TEMP:-/tmp}/deploy-report}"
now="$(date +%s.%N)"
# Без GNU date (`%N` не раскрылся) — целые секунды.
now="${now%.N}"
if ! { mkdir -p "$dir" && printf '%s %s\n' "${GITHUB_ACTION:-unknown}" "$now" >> "$dir/marks"; }; then
  echo "::warning::засечка шага ${GITHUB_ACTION:-unknown} не записана"
fi
exit 0
