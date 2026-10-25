# Tasks

## 1. SSH

- [x] 1.1 Composite action `.github/actions/stand-ssh`: ключ, known_hosts, хост `stand` в `~/.ssh/config`
- [x] 1.2 `s3-backend`: проверка хоста `stand` вместо настройки SSH, без входов `ssh_key`, `ssh_user`, `host` и без неиспользуемых выходов
- [x] 1.3 `deploy.yml`: `stand-ssh` первым шагом после checkout, вызовы — `ssh stand`, `TF_VAR_docker_host: ssh://stand`

## 2. Скрипты

- [x] 2.1 `.github/scripts/deploy/diagnostics.sh`, `check-api.sh`, `check-worker.sh` с пояснениями в шапках; в YAML — строка вызова
- [x] 2.2 shellcheck по `.github/scripts/**/*.sh` в job'е `zizmor` в `ci.yml`

## 3. Раскладка job'а

- [x] 3.1 `TF_VAR_*` — в `env` шага apply, адрес стенда — в `env` job'а
- [x] 3.2 Уборка образов — после проверок
- [x] 3.3 Summary с адресом `https://<HOST>/api/v1/`; `api_url`, `postgres_url`, `rabbitmq_url` и `service_host` удалены из `infra/`

## 4. Проверки

- [x] 4.1 actionlint, zizmor, shellcheck, `tofu fmt`/`validate`/`test`
- [x] 4.2 Скрипты и `stand-ssh` прогнаны против локального sshd с Docker, docker CLI и провайдер — через `ssh://stand`
