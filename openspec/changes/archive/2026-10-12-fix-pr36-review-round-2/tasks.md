## 1. Адаптер очереди

- [x] 1.1 `app/infrastructure/queue/rabbitmq.py`: `RabbitMQJobQueue.enqueue` —
      при `AMQPConnectionError`/`ChannelWrongStateError` закрывает соединение
      (`_drop_connection`), переподключается и публикует один раз повторно.
- [x] 1.2 `tests/queue/test_channel_liveness.py`: обрыв соединения даёт
      переподключение и ровно одну повторную публикацию; второй отказ
      пробрасывается.

## 2. Конфигурация стенда

- [x] 2.1 `infra/main.tf`, `infra/variables.tf`: `RABBITMQ_URL` и
      `GITHUB_WEBHOOK_SECRET` в `env` контейнеров `migrate` и `api`; sensitive
      переменная `github_webhook_secret`.
- [x] 2.2 `.github/workflows/deploy.yml`, `infra/README.md`:
      `TF_VAR_github_webhook_secret` из `WEBHOOK_SECRET_DMC268_T1`.
- [x] 2.3 Завести секрет репозитория `WEBHOOK_SECRET_DMC268_T1`
      (значение совпадает с секретом вебхука GitHub). Делает владелец
      репозитория до мёржа.

## 3. Версия брокера

- [x] 3.1 `docker-compose.yml`, `.github/workflows/ci.yml`: `rabbitmq:4.3-alpine`.
