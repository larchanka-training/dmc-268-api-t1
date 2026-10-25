# Общие значения стека: ротация логов, окружение процессов из образа
# приложения, телеметрия (экспорт и версия сервиса) и грейс-период остановки
# воркера.

locals {
  # Ротация логов: без неё json-file растёт, пока не кончится диск сервера.
  # Три файла по 10 МБ на контейнер хватает, чтобы разобрать падение после деплоя.
  log_opts = {
    "max-size" = "10m"
    "max-file" = "3"
  }

  # Версия сервиса для телеметрии — тег образа API: deploy.yml публикует образ
  # с тегом по sha коммита, а git внутрь образа не попадает, поэтому версия
  # приходит окружением (OTEL_RESOURCE_ATTRIBUTES). Тег — между последним
  # двоеточием и концом или «@дайджестом»: реестр с портом
  # (localhost:5000/repo:sha) и формат с дайджестом (repo:tag@sha256:hex —
  # после #204) не ломают разбор (замечание ревью PR #130).
  service_version = try(
    regex(":(?P<tag>[^/:@]+)@", var.api_image)["tag"],
    regex(":(?P<tag>[^/:@]+)$", var.api_image)["tag"],
    "unknown",
  )

  # Экспорт телеметрии сервисами приложения (change add-observability-stack):
  # коллектор в той же сети, логи и трейсы с метриками идут по OTLP. Таймаут
  # OTLP меньше окна graceful shutdown API (5 с), чтобы выгрузка спанов при
  # остановке успевала даже при недоступном коллекторе (замечание из #89).
  # Python SDK читает таймаут в СЕКУНДАХ, вопреки спеке OTel (мс).
  # Стек выключен — список пуст: SDK без OTEL_*_EXPORTER ничего не отправляет
  # и в лог ошибок не пишет, а concat в env api и воркера не ломается.
  telemetry_env = var.observability_enabled ? [
    "OTEL_TRACES_EXPORTER=otlp",
    "OTEL_METRICS_EXPORTER=otlp",
    "OTEL_LOGS_EXPORTER=otlp",
    "OTEL_EXPORTER_OTLP_ENDPOINT=http://dmc268-otelcol:4318",
    "OTEL_EXPORTER_OTLP_TIMEOUT=4",
  ] : []

  # Общая часть OTEL_RESOURCE_ATTRIBUTES; service.instance.id каждый контейнер
  # дописывает своим стабильным именем (см. env ниже): случайный UUID на
  # процесс растил бы в Prometheus серии exported_instance — ресурс
  # конвертируется в лейблы рядов.
  telemetry_resource = "service.version=${local.service_version},deployment.environment.name=${var.deployment_environment_name}"

  # Образы стека наблюдаемости — те же версии, что в docker-compose.yml:
  # конфиги в observability/ общие. Зафиксированы дайджестом манифест-листа
  # (multi-arch, разрешён `docker buildx imagetools inspect <имя:тег>`) —
  # одинаково в обоих местах; тег в комментарии рядом, обновление образа —
  # осознанная замена дайджеста в обоих файлах с чтением breaking changes.
  otelcol_image       = "otel/opentelemetry-collector-contrib@sha256:c5918f78992ee73b0d6f0e599423ac5ec52dd5d9726733114d6eca53d5a32ed5"
  loki_image          = "grafana/loki@sha256:0db216b630ae1f348a1584c67f3cead8b4c1fc839169a39501db664744158e28"
  tempo_image         = "grafana/tempo@sha256:64e2fbd56d9c2b3412a4b430a58ea59e91a670b1e75f920a0c702213ad145044"
  prometheus_image    = "prom/prometheus@sha256:fa3302279710be2f21a8301ec17a8b4f2301fd829bd113839b3b6b02b6f191a2"
  grafana_image       = "grafana/grafana@sha256:dbbc38ea21103cefb50396340cd88afec779c7e070b0a7bf5a75b4ce46651d3f"
  node_exporter_image = "quay.io/prometheus/node-exporter@sha256:d00a542e409ee618a4edc67da14dd48c5da66726bbd5537ab2af9c1dfc442c8a"
  cadvisor_image      = "gcr.io/cadvisor/cadvisor@sha256:f40e65878e25c2e78ea037f73a449527a0fb994e303dc3e34cb6b187b4b91435"
  # socket-proxy: доступ cAdvisor к Docker API без docker.sock (контейнеры и
  # информация о сервере; минимум прав — см. env у контейнера в observability.tf).
  docker_socket_proxy_image = "tecnativa/docker-socket-proxy@sha256:3400c429c5f9e1b21d62130fb93b16e2e772d4fb7695bd52fc2b743800b9fe9e"

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
  # consumer_timeout RabbitMQ его не ограничивает: в 4.4 таймаут подтверждения
  # есть только у quorum-очередей, а очередь review — classic.
  # Деплой ждёт остановку воркера в `tofu apply`: timeout-minutes job deploy в
  # .github/workflows/deploy.yml обязан быть больше максимума (2460 с) с запасом.
  # Та же формула — у stop_grace_period воркера в docker-compose.yml.
  worker_stop_seconds = 2 * 300 * 2 * (var.llm_hosted_fallback ? 2 : 1) + 60
}
