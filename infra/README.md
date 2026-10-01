# Стек сервера (API на Python 3.14, PostgreSQL, RabbitMQ)

Нужны **Docker Engine** на целевом хосте и Terraform >= 1.5. Образ API собирается из `Dockerfile` с базой `python:3.14-slim` (`python_version`).

PostgreSQL — образ `pgvector/pgvector:pg18`: профиль репозитория (RAG) держит
векторы в той же базе, расширение `pgvector` создаёт миграция `0003`. Ollama в
стек не входит: `ollama_base_url` указывает на существующий сервис (по
умолчанию — на хост-машину, порт 11434); недоступность Ollama не мешает ревью,
профиль просто не пополняется.

## Локальный или удалённый Docker

Скопируйте `terraform.tfvars.example` в `terraform.tfvars` и замените плейсхолдеры `CHANGE_ME_*`. Файл `terraform.tfvars` не коммитится (gitignore).

- Локальный демон: `docker_host = "unix:///var/run/docker.sock"` (у Docker Desktop на macOS часто `unix:///Users/<вы>/.docker/run/docker.sock`).
- Удалённый сервер: `docker_host = "ssh://user@server"` (нужны SSH-доступ и Docker на удалённом хосте).

PostgreSQL и RabbitMQ публикуются на `internal_bind_ip` (по умолчанию `127.0.0.1`), API публикуется на `bind_ip`. В URL для клиентов используется `service_host`, его нельзя ставить в `0.0.0.0`.

## Развёртывание и удаление

```bash
cd infra
terraform init
terraform apply
terraform destroy
```

Выходы: `api_url`; чувствительные: `postgres_url`, `rabbitmq_url` (пароли в URL кодируются через `urlencode`).

Этот стек только для API. UI деплоится отдельно из `dmc-268-ui-t1/infra/`.
