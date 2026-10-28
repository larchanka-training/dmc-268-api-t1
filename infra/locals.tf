# Общие значения стека: ротация логов, окружение процессов из образа
# приложения и грейс-период остановки воркера.

locals {
  # Ротация логов: без неё json-file растёт, пока не кончится диск сервера.
  # Три файла по 10 МБ на контейнер хватает, чтобы разобрать падение после деплоя.
  log_opts = {
    "max-size" = "10m"
    "max-file" = "3"
  }

  # Окружение, без которого не загрузятся общие настройки (`load_settings`):
  # его получают оба контейнера из образа приложения — api и worker — и
  # миграции шага деплоя (выход migrate_env). Одно описание вместо трёх копий,
  # чтобы они не разъезжались.
  #
  # Хосты — ссылки на контейнеры, а не строки: так migrate_env зависит от базы
  # и брокера, и `tofu apply -target` для них перед миграциями обновляет выход
  # в состоянии. Выход, не зависящий от целевых ресурсов, targeted apply не
  # записывает (проверено на OpenTofu 1.13.2), и миграции получили бы
  # окружение прошлого выката — или никакого на первом.
  app_env = [
    "DATABASE_URL=postgresql+psycopg://${urlencode(var.postgres_user)}:${urlencode(var.postgres_password)}@${docker_container.postgres.name}:5432/${var.postgres_db}",
    # %2F — закодированный дефолтный vhost "/": pika разбирает буквальный "//" как пустой vhost.
    "RABBITMQ_URL=amqp://${urlencode(var.rabbitmq_user)}:${urlencode(var.rabbitmq_password)}@${docker_container.rabbitmq.name}:5672/%2F",
    "GITHUB_WEBHOOK_SECRET=${var.github_webhook_secret}",
  ]

  # Грейс-период остановки воркера, секунды; обоснование — у docker_container.worker.
  # Очередь review — quorum, и таймаут подтверждения брокера к ней применяется:
  # он задан аргументом очереди (CONSUMER_TIMEOUT_SECONDS в
  # app/infrastructure/queue/rabbitmq.py, 2 ч) с запасом больше этого грейса,
  # воркер проверяет запас на старте (check_consumer_timeout).
  # Деплой ждёт остановку воркера в `tofu apply`: timeout-minutes job deploy в
  # .github/workflows/deploy.yml обязан быть больше максимума (2460 с) с запасом.
  # Та же формула — у stop_grace_period воркера в docker-compose.yml.
  worker_stop_seconds = 2 * 300 * 2 * (var.llm_hosted_fallback ? 2 : 1) + 60
}
