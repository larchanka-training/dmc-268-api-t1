# Общие значения стека: ротация логов, окружение контейнеров из образа
# приложения и грейс-период остановки воркера.

locals {
  # Ротация логов: без неё json-file растёт, пока не кончится диск сервера.
  # Три файла по 10 МБ на контейнер хватает, чтобы разобрать падение после деплоя.
  log_opts = {
    "max-size" = "10m"
    "max-file" = "3"
  }

  # Окружение, без которого не загрузятся общие настройки (`load_settings`):
  # его получают все три контейнера из образа приложения — migrate, api и
  # worker. Одно описание вместо трёх копий, чтобы они не разъезжались.
  app_env = [
    "DATABASE_URL=postgresql+psycopg://${urlencode(var.postgres_user)}:${urlencode(var.postgres_password)}@dmc268-postgres:5432/${var.postgres_db}",
    # %2F — закодированный дефолтный vhost "/": pika разбирает буквальный "//" как пустой vhost.
    "RABBITMQ_URL=amqp://${urlencode(var.rabbitmq_user)}:${urlencode(var.rabbitmq_password)}@dmc268-rabbitmq:5672/%2F",
    "GITHUB_WEBHOOK_SECRET=${var.github_webhook_secret}",
  ]

  # Грейс-период остановки воркера, секунды; обоснование — у docker_container.worker.
  # consumer_timeout RabbitMQ его не ограничивает: в 4.4 таймаут подтверждения
  # есть только у quorum-очередей, а очередь review — classic.
  # Деплой ждёт остановку воркера в `tofu apply`: timeout-minutes job deploy в
  # .github/workflows/deploy.yml обязан быть больше максимума (2460 с) с запасом.
  # Та же формула — у stop_grace_period воркера в docker-compose.yml.
  worker_stop_seconds = 2 * 300 * 2 * (var.llm_hosted_fallback ? 2 : 1) + 60
}
