# Стек сервера (API на Python 3.14, PostgreSQL, RabbitMQ)

Нужны **Docker Engine** на целевом хосте и Terraform >= 1.5. Образ API собирается из `Dockerfile` с базой `python:3.14-slim` (`python_version`).

## Локальный или удалённый Docker

Скопируйте `terraform.tfvars.example` в `terraform.tfvars` и замените плейсхолдеры `CHANGE_ME_*`. Файл `terraform.tfvars` не коммитится (gitignore).

- Локальный демон: `docker_host = "unix:///var/run/docker.sock"` (у Docker Desktop на macOS часто `unix:///Users/<вы>/.docker/run/docker.sock`).
- Удалённый сервер: `docker_host = "ssh://user@server"` (нужны SSH-доступ и Docker на удалённом хосте).

PostgreSQL и RabbitMQ публикуются на `internal_bind_ip` (по умолчанию `127.0.0.1`). API — на `bind_ip`. В URL для клиентов используется `service_host`, его нельзя ставить в `0.0.0.0`.

## Apply / destroy

```bash
cd infra
terraform init
terraform apply
terraform destroy
```

Выходы: `api_url`; чувствительные: `postgres_url`, `rabbitmq_url` (пароли в URL кодируются через `urlencode`).

Этот стек только для API. UI деплоится отдельно из `dmc-268-ui-t1/infra/`.
