# Стек сервера (PostgreSQL, RabbitMQ, Redis)

Нужны **Docker Engine** на целевом хосте и Terraform >= 1.5.

## Локальный или удалённый Docker

Скопируйте `terraform.tfvars.example` в `terraform.tfvars` и замените плейсхолдеры `CHANGE_ME_*`. Файл `terraform.tfvars` не коммитится (gitignore).

- Локальный демон: `docker_host = "unix:///var/run/docker.sock"` (у Docker Desktop на macOS часто `unix:///Users/<вы>/.docker/run/docker.sock`).
- Удалённый сервер: `docker_host = "ssh://user@server"` (нужны SSH-доступ и Docker на удалённом хосте).

Порты по умолчанию публикуются на `127.0.0.1`. Меняйте `bind_ip` только в частной сети.

## Apply / destroy

```bash
cd infra
terraform init
terraform apply
terraform destroy
```

Выходы (чувствительные): `postgres_url`, `rabbitmq_url`, `redis_url`.

Этот стек только для API. UI деплоится отдельно из `dmc-268-ui-t1/infra/`.
