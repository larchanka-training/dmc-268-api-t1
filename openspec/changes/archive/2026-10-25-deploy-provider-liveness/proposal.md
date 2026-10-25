# Proposal

## Why

`tofu apply` считает контейнер API готовым, как только тот запущен: `wait` у контейнеров не включён, а healthcheck с `interval = "30s"` и без `start_interval` даёт `healthy` только через ~30 с (#160). API, который стартует и не отвечает, деплой замечает лишь туннельной проверкой `/health`, и то через минуту ожидания.

## What Changes

- У `docker_container.api` `wait = true`, `wait_timeout = 90`: apply падает, если новый API не стал `healthy` за 90 с.
- Healthcheck API — `start_interval = "2s"`; длительности в формате Go, как их нормализует провайдер. Описание healthcheck вынесено в `local.api_healthcheck`, и `terraform_data.api_healthcheck` пересоздаёт API через `replace_triggered_by`: правку healthcheck провайдер 4.6.0 планирует на месте, но к запущенному контейнеру она не применяется — вечный diff.
- `deploy.yml`: шаг с логом и историей проверок API, если apply упал.
- `check-api.sh`: туннельная проверка остаётся (путь снаружи), ожидание сокращено с минуты до 10 с.

**Не входит:** healthcheck и `wait` у воркера — нужен heartbeat из #157; до него устойчивость воркера проверяет `check-worker.sh`.

## Capabilities

### Modified Capabilities

- `backend-delivery`: развёртывание дожидается готовности API и падает, если тот не стал готов за отведённое время.

## Impact

- `infra/app.tf`, `infra/tests/main.tftest.hcl`, `infra/README.md`.
- `.github/workflows/deploy.yml`, `.github/scripts/deploy/check-api.sh`.
- Первый деплой после мёржа пересоздаёт API один раз (новый `terraform_data`), простой — как при обычной замене образа.
