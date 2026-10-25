# Proposal

## Why

Job `deploy` в `deploy.yml` — почти 250 строк, из них около 84 — встроенный bash. SSH к
стенду настраивает `s3-backend` побочным эффектом, и остальные шаги неявно от него зависят:
поставь шаг раньше — «Permission denied». `SSH_USER` и `HOST` повторены в пяти `env`,
`TF_VAR_*` выставляются `export` в `run`, уборка образов идёт до проверок, выходы
`s3-backend` никто не читает. Summary показывает `tofu output api_url` — `http://HOST:8000`,
а порт API снаружи закрыт. Закрывает #193.

## What Changes

- Composite action `.github/actions/stand-ssh` — первым шагом после checkout: ключ,
  закреплённый known_hosts и хост `stand` в `~/.ssh/config` (HostName, User, IdentityFile,
  BatchMode). Все вызовы — `ssh stand …`, docker-провайдер — `ssh://stand`.
- `s3-backend` SSH не настраивает и входов `ssh_key`, `ssh_user`, `host` не принимает: первым
  шагом проверяет хост `stand` в `~/.ssh/config` и падает с понятной ошибкой. Неиспользуемые
  выходы `storage`, `bucket`, `versioning` удалены.
- Диагностика вебхуков, проверки API и воркера — скрипты `.github/scripts/deploy/`
  (`diagnostics.sh`, `check-api.sh`, `check-worker.sh`), в YAML — по строке вызова. Логика
  `-replace` для migrate остаётся в шаге apply: её убирает #159.
- `TF_VAR_*` — в `env` шага apply, адрес стенда — в `env` job'а.
- Уборка образов — после проверок, перед summary.
- Summary — публичный адрес `https://<HOST>/api/v1/`. Выходы `api_url`, `postgres_url`,
  `rabbitmq_url` и переменная `service_host`, которую читали только они, удалены из `infra/`.
- shellcheck по `.github/scripts/**/*.sh` — шагом в job'е `zizmor` в `ci.yml`.

## Capabilities

### New Capabilities

Нет.

### Modified Capabilities

Нет.

`skip_specs: true`: требования `backend-delivery` к деплою не меняются — те же проверки
`/health` и воркера, та же остановка на упавших миграциях. Адрес в summary и раскладка
шагов в спеке не описаны.

## Impact

- `.github/workflows/deploy.yml`, `.github/actions/stand-ssh/`, `.github/actions/s3-backend/`,
  `.github/scripts/deploy/`, `.github/workflows/ci.yml`.
- `infra/outputs.tf` удалён, из `infra/variables.tf` и `infra/terraform.tfvars.example` ушла
  `service_host`, `infra/README.md` приведён в соответствие.
- Главная проверка — первый деплой после мёржа: SSH через алиас `stand` у docker-провайдера
  на стенде ещё не ходил.
