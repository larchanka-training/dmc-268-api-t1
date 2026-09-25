# Стек сервера (API на Python 3.14, PostgreSQL, RabbitMQ)

Нужны **Docker Engine** на целевом хосте и OpenTofu >= 1.10 (в CI ставится 1.10.5). Образ API не собирается здесь: он публикуется в ghcr из CI и забирается по тегу (`api_image`).

## Локальный или удалённый Docker

Скопируйте `terraform.tfvars.example` в `terraform.tfvars` и замените плейсхолдеры `CHANGE_ME_*`. Файл `terraform.tfvars` не коммитится (gitignore).

- Локальный демон: `docker_host = "unix:///var/run/docker.sock"` (у Docker Desktop на macOS часто `unix:///Users/<вы>/.docker/run/docker.sock`).
- Удалённый сервер: `docker_host = "ssh://user@server"` (нужны SSH-доступ и Docker на удалённом хосте).

PostgreSQL и RabbitMQ публикуются на `internal_bind_ip` (по умолчанию `127.0.0.1`), API публикуется на `bind_ip`. В URL для клиентов используется `service_host`, его нельзя ставить в `0.0.0.0`.

## Деплой

Стенд катится сам при мерже в `develop` (`.github/workflows/deploy.yml`). `main` в деплое не участвует: сервер один, и два источника дрались бы за него.

Порядок такой: образ собирается и публикуется в `ghcr.io/larchanka-training/dmc-268-api-t1` с тегом по commit sha → поднимается хранилище состояния и туннель к нему → `tofu apply` по ключу CI → проверка `/health` через туннель, затем по публичному адресу.

**Миграции применяются отдельным контейнером** `dmc268-migrate`, от которого зависит API. Он ждёт, пока база начнёт принимать соединения, выполняет `alembic upgrade head` и завершается; `postcondition` на его код возврата останавливает деплой до старта API, если миграция упала. Проверка `/health` этого не заметила бы: по контракту она отвечает независимо от базы.

Переменные, без которых `apply` не пройдёт (в CI приходят через `TF_VAR_*`):

| Переменная | Откуда |
| --- | --- |
| `api_image` | тег собранного образа |
| `postgres_password`, `rabbitmq_password` | секреты `PG_PASSWORD_DMC268_T1`, `RABBITMQ_PASSWORD_DMC268_T1` |
| `registry_username`, `registry_password` | `github.actor` и `GITHUB_TOKEN`, живут один прогон |
| `docker_host`, `service_host` | собираются из `VPS_DMC268_U` и `VPS_DMC268_IP_T1` |
| `bind_ip` | `0.0.0.0` для API; база и брокер остаются на `internal_bind_ip` |

Наружу смотрит только API. PostgreSQL и RabbitMQ публикуются на петле сервера: к ним ходит API изнутри docker-сети.

## Хранилище состояния

Состояние Terraform лежит не рядом с конфигурацией, а в S3-совместимом хранилище (RustFS) на том же сервере (`backend.tf`). Так оно переживает прогон раннера GitHub Actions, который исчезает вместе со своим диском.

Хранилище слушает только `127.0.0.1`, поэтому `tofu` любой командой, которой нужно состояние, работает через SSH-туннель:

```bash
ssh -i <ключ> -o ExitOnForwardFailure=yes -f -N -L 9000:127.0.0.1:9000 <user>@<server>
```

Учётные данные хранилища передаются как `AWS_ACCESS_KEY_ID` и `AWS_SECRET_ACCESS_KEY`; в CI они приходят из секретов `S3_USER_DMC268_T1` и `S3_PASSWORD_DMC268_T1`. Состояния API и UI лежат в одном бакете `dmc268-tfstate` под разными ключами.

Сам контейнер хранилища **не входит в этот стек**: им управляет workflow `tfstate-backend.yml`, а не Terraform, иначе `apply` мог бы снести хранилище собственного состояния. Практическое следствие: `tofu destroy` его не удалит, и полная зачистка сервера состоит из двух шагов — `tofu destroy`, а затем `docker rm -f dmc268-s3` и `docker volume rm dmc268-s3-data` вручную.

## Развёртывание и удаление

```bash
cd infra
terraform init
terraform apply
terraform destroy
```

Выходы: `api_url`; чувствительные: `postgres_url`, `rabbitmq_url` (пароли в URL кодируются через `urlencode`).

Этот стек только для API. UI деплоится отдельно из `dmc-268-ui-t1/infra/`.
