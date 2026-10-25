# Tasks

## 1. Сторож и план в файл

- [x] 1.1 `scripts/deploy_report.py`: `plan_changes` и `protected_deletes` — чистые функции над JSON плана; команда `plan` пишет `plan.json` и падает на `delete` защищённого тома
- [x] 1.2 `.github/scripts/deploy/plan.sh`: `tofu plan -out=tfplan`, `tofu show -json` трубой в `deploy_report.py plan`, отклонённый план удаляется
- [x] 1.3 `.github/scripts/deploy/apply.sh`: `tofu apply tfplan`, файл плана удаляется на выходе

## 2. Сырьё для отчёта

- [x] 2.1 `apply.sh`: id старого API, поток `docker events` по `dmc268-api`, `docker inspect` нового (`Id`, `Created`, `State`)
- [x] 2.2 `migrate.sh`: вывод alembic в `migrate.log` каталога отчёта
- [x] 2.3 `mark.sh`: засечка `<id шага> <время>`; `summary.sh`: `df -Pk` корня и каталога Docker, `deploy_report.py render`

## 3. Summary

- [x] 3.1 `deploy_report.py`: `api_downtime`, `migrations`, `disk`, `durations`, `step_rows`, `failed_step` — чистые функции; `render` — markdown с ключами OpenTelemetry и `dmc268.deploy.*`
- [x] 3.2 `deploy.yml`: id у всех шагов job'а `deploy`, засечка первой строкой шагов с `run`, шаги плана и apply через скрипты, `STEP_RESULTS` в шаге summary, `tofu_wrapper: false`, вход в ghcr — в подготовке

## 4. Проверки

- [x] 4.1 `tests/test_deploy_report.py`: сторож на фикстурах `tofu show -json` 1.13.2, `plan.sh`/`apply.sh`/`summary.sh` bash'ем с подложными `tofu` и `ssh`, разбивка простоя на событиях и inspect из репетиции, раскладка job'а против `STEPS`
- [x] 4.2 `tests/test_stack_descriptions.py`: полный apply — шаг `apply.sh` после миграций
- [x] 4.3 Репетиция на `docker:29-dind`: выкат старый `infra/` → текущий, план с заменой тома (сторож), смена тома через `removed`, повтор без изменений
- [x] 4.4 shellcheck, zizmor, actionlint, `tofu fmt`/`validate`/`test`, ruff, mypy, pytest, `openspec validate`
