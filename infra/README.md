# Стек сервера (API на Python 3.14, PostgreSQL, RabbitMQ)

Нужны **Docker Engine** на целевом хосте и Terraform >= 1.5. Образ API собирается из `Dockerfile` с базой `python:3.14-slim` (`python_version`).

## Локальный или удалённый Docker

Скопируйте `terraform.tfvars.example` в `terraform.tfvars` и замените плейсхолдеры `CHANGE_ME_*`. Файл `terraform.tfvars` не коммитится (gitignore).

- Локальный демон: `docker_host = "unix:///var/run/docker.sock"` (у Docker Desktop на macOS часто `unix:///Users/<вы>/.docker/run/docker.sock`).
- Удалённый сервер: `docker_host = "ssh://user@server"` (нужны SSH-доступ и Docker на удалённом хосте).

PostgreSQL и RabbitMQ публикуются на `internal_bind_ip` (по умолчанию `127.0.0.1`), API публикуется на `bind_ip`. В URL для клиентов используется `service_host`, его нельзя ставить в `0.0.0.0`.

## Хранилище состояния

Состояние Terraform лежит не рядом с конфигурацией, а в MinIO на том же сервере (`backend.tf`). Так оно переживает прогон раннера GitHub Actions, который исчезает вместе со своим диском.

MinIO слушает только `127.0.0.1`, поэтому `tofu` любой командой, которой нужно состояние, работает через SSH-туннель:

```bash
ssh -i <ключ> -o ExitOnForwardFailure=yes -f -N -L 9000:127.0.0.1:9000 <user>@<server>
```

Учётные данные хранилища передаются как `AWS_ACCESS_KEY_ID` и `AWS_SECRET_ACCESS_KEY`; в CI они приходят из секретов `MINIO_USER_DMC268_T1` и `MINIO_PASSWORD_DMC268_T1`. Состояния API и UI лежат в одном бакете `dmc268-tfstate` под разными ключами.

Сам контейнер MinIO **не входит в этот стек**: им управляет workflow `tfstate-backend.yml`, а не Terraform, иначе `apply` мог бы снести хранилище собственного состояния. Практическое следствие: `tofu destroy` его не удалит, и полная зачистка сервера состоит из двух шагов — `tofu destroy`, а затем `docker rm -f dmc268-minio` и `docker volume rm dmc268-minio-data` вручную.

## Развёртывание и удаление

```bash
cd infra
terraform init
terraform apply
terraform destroy
```

Выходы: `api_url`; чувствительные: `postgres_url`, `rabbitmq_url` (пароли в URL кодируются через `urlencode`).

Этот стек только для API. UI деплоится отдельно из `dmc-268-ui-t1/infra/`.
