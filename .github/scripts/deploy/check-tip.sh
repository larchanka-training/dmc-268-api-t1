#!/usr/bin/env bash
# Катить ли коммит: он всё ещё вершина develop? (спека backend-delivery, «На
# стенд попадает только проверенный коммит», сценарий «Перезапуск устаревшего
# прогона»).
#
# gate сверяет вершину только у workflow_run и только в момент своего запуска.
# «Re-run failed jobs» успешный gate не перезапускает: берётся его выход `sha`
# из первой попытки (docs.github.com, «Re-running workflows and jobs»: «Any
# outputs for any successful jobs in the previous workflow run will be used
# for the re-run»), — и упавший деплой A, перезапущенный после выката B,
# положил бы A поверх B. Ручной запуск gate не сверяет вовсе. Перезапускается
# job deploy, поэтому сверка — его шагом, перед первым обращением к стенду.
#
# Вершина ушла — выход `deploy=false`, `::notice::` и файл stale-tip в
# каталоге отчёта: остальные шаги job'а с `if: *fresh` пропускаются, job
# зелёный, summary пишет «пропущен». Новый коммит катит свой прогон.
# Не удалось спросить GitHub — шаг падает: без сверки не катим.
#
# Нужны (env): SHA — коммит из gate, GH_TOKEN — токен с contents: read,
# GITHUB_REPOSITORY, GITHUB_OUTPUT. REPORT_DIR — каталог отчёта, по умолчанию
# $RUNNER_TEMP/deploy-report.
set -euo pipefail

: "${SHA:?нужен коммит из gate}"
dir="${REPORT_DIR:-${RUNNER_TEMP:-/tmp}/deploy-report}"
mkdir -p "$dir"

tip="$(gh api "repos/${GITHUB_REPOSITORY:?}/commits/develop" --jq .sha)"
if [ -z "$tip" ]; then
  echo "::error::GitHub не вернул вершину develop"
  exit 1
fi

if [ "$tip" = "$SHA" ]; then
  echo "${SHA} — вершина develop, катим"
  echo "deploy=true" >> "${GITHUB_OUTPUT:?}"
  exit 0
fi

echo "::notice::${SHA} уже не вершина develop (${tip}), деплой пропущен"
echo "$tip" > "$dir/stale-tip"
echo "deploy=false" >> "${GITHUB_OUTPUT:?}"
