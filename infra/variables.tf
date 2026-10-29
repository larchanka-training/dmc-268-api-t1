# --- Docker и публикация портов ---

variable "docker_host" {
  type        = string
  description = "URI демона Docker: локально unix:///var/run/docker.sock, для удалённого хоста ssh://user@server."
  default     = "unix:///var/run/docker.sock"
}

variable "bind_ip" {
  type        = string
  description = "Интерфейс, на котором публикуется порт API. Как имя хоста для клиентов не используется."
  default     = "127.0.0.1"

  # Спека backend-delivery: порт API не выходит за пределы сервера. Гарантирует
  # это только петля; ловим ошибку на plan, пока на сервере ничего не изменилось.
  validation {
    condition     = can(regex("^127\\.", var.bind_ip)) || var.bind_ip == "::1"
    error_message = "bind_ip должен быть адресом петли (127.0.0.0/8 или ::1): иначе порт API окажется доступен снаружи."
  }
}

variable "api_port" {
  type        = number
  description = "Порт хоста для HTTP-сервера FastAPI."
  default     = 8000
}

# --- PostgreSQL ---

variable "postgres_user" {
  type        = string
  description = "Пользователь PostgreSQL."
  default     = "dmc268"
}

# Незаданный секрет репозитория приходит в TF_VAR_* пустой строкой. Без
# проверки apply пересоздал бы базу и брокер с пустым паролем, а упал бы
# только минутой позже, на миграциях, — уже со сломанным стендом.
variable "postgres_password" {
  type        = string
  description = "Пароль PostgreSQL. Реальные значения не коммитить."
  sensitive   = true

  validation {
    condition     = var.postgres_password != ""
    error_message = "postgres_password пуст: секрет PG_PASSWORD_DMC268_T1 не задан."
  }
}

variable "postgres_db" {
  type        = string
  description = "Имя базы PostgreSQL."
  default     = "dmc268"
}

# --- Реплика и резервные копии PostgreSQL ---

# Пароль роли replicator. Пустой или короткий — роль без входа, и реплика не
# соберётся; ловим на plan, пока на сервере ничего не изменилось.
variable "postgres_replication_password" {
  type        = string
  description = "Пароль роли replicator, под которой реплика забирает поток с основного сервера. Реальные значения не коммитить."
  sensitive   = true

  validation {
    condition     = length(var.postgres_replication_password) >= 16
    error_message = "postgres_replication_password пуст или короче 16 символов: секрет PG_REPLICATION_PASSWORD_DMC268_T1 не задан."
  }
}

variable "postgres_max_slot_wal_keep_size" {
  type        = string
  description = "Сколько WAL основной сервер держит для отставшей реплики (max_slot_wal_keep_size). Потолок защищает диск от упавшей реплики."
  default     = "2GB"
}

variable "postgres_replica_memory" {
  type        = number
  description = "Потолок памяти реплики, МБ."
  default     = 512
}

variable "backup_keep_days" {
  type        = number
  description = "Сколько суток хранить резервные копии базы."
  default     = 7
}

# --- RabbitMQ ---

variable "rabbitmq_user" {
  type        = string
  description = "Пользователь RabbitMQ."
  default     = "dmc268"
}

variable "rabbitmq_password" {
  type        = string
  description = "Пароль RabbitMQ. Реальные значения не коммитить."
  sensitive   = true

  validation {
    condition     = var.rabbitmq_password != ""
    error_message = "rabbitmq_password пуст: секрет RABBITMQ_PASSWORD_DMC268_T1 не задан."
  }
}

# --- GitHub App, вход пользователей и вебхуки ---

variable "github_app_id" {
  type        = string
  description = "ID GitHub App (GITHUB_APP_ID для контейнеров API и воркера)."
  default     = ""
}

variable "github_app_private_key" {
  type        = string
  description = "Закрытый ключ GitHub App в PEM (GITHUB_APP_PRIVATE_KEY). Реальные значения не коммитить."
  default     = ""
  sensitive   = true
}

variable "github_app_client_id" {
  type        = string
  description = "Client ID GitHub App для входа пользователей (GITHUB_APP_CLIENT_ID). Пустой — вход не работает."
  default     = ""
}

variable "github_app_client_secret" {
  type        = string
  description = "Client secret GitHub App для входа пользователей (GITHUB_APP_CLIENT_SECRET). Не коммитить."
  default     = ""
  sensitive   = true
}

# Без значения по умолчанию и с проверкой формата: стенд без ключа не
# поднимется молча с неработающим входом — apply упадёт здесь же.
variable "user_token_encryption_key" {
  type        = string
  description = "Ключ шифрования токенов пользователя GitHub (USER_TOKEN_ENCRYPTION_KEY): 32 байта в base64, openssl rand -base64 32. Не коммитить."
  sensitive   = true

  validation {
    condition     = can(regex("^[A-Za-z0-9+/]{43}=$", var.user_token_encryption_key))
    error_message = "user_token_encryption_key - 32 байта в base64 (44 символа, openssl rand -base64 32); секрет USER_TOKEN_KEY_DMC268_T1 не задан или битый."
  }
}

variable "public_origin" {
  type        = string
  description = "Публичный origin интерфейса без завершающего / (PUBLIC_ORIGIN): сверка Origin и callback входа. Пустой — изменяющие запросы с сессией получают 403."
  default     = ""
}

variable "github_webhook_secret" {
  type        = string
  description = "Общий секрет, которым GitHub подписывает доставки вебхуков (X-Hub-Signature-256). Не коммитить."
  sensitive   = true

  # Пустой секрет — HMAC с ключом, который знает каждый. Приложение такой не
  # примет и не стартует; ловим раньше, на plan.
  validation {
    condition     = var.github_webhook_secret != ""
    error_message = "github_webhook_secret пуст: секрет WEBHOOK_SECRET_DMC268_T1 не задан."
  }
}

# --- Модель ---

# Без значения по умолчанию: заглушка выбирается только явно, иначе стенд
# молча публиковал бы пустые ревью (спека llm-gateway, «Выбор заглушки явный»).
variable "llm_backend" {
  type        = string
  description = "Адаптер модели воркера (LLM_BACKEND): stub - без похода к модели, ollama - локальная модель."

  validation {
    condition     = contains(["stub", "ollama"], var.llm_backend)
    error_message = "llm_backend - stub или ollama."
  }
}

# Стенд и есть прод (#97): значение одно и то же у API, воркера и кабинета —
# иначе фильтр по окружению в Grafana разъедется (#108).
variable "deployment_environment_name" {
  type        = string
  description = "deployment.environment.name в OTEL_RESOURCE_ATTRIBUTES сервисов приложения."
  default     = "production"

  validation {
    condition     = var.deployment_environment_name == "production"
    error_message = "Стенд и есть прод (#97): значение должно быть production."
  }
}

# Стек наблюдаемости поднимается только по флагу: локальному apply без него
# хватает памяти, а секреты (пароль админа Grafana, пароль роли мониторинга
# базы) нужны только вместе со стеком — см. валидации у их переменных.
# Деплой стенда включает его (deploy.yml, TF_VAR_observability_enabled).
variable "observability_enabled" {
  type        = bool
  description = "Поднимать стек наблюдаемости: коллектор, Loki, Tempo, Prometheus, Grafana, node-exporter и socket-proxy."
  default     = false
}

# Семь контейнеров стека на VPS — риск из design.md change'а
# add-observability-stack (socket-proxy добавлен позже, cAdvisor убран — #249);
# потолки памяти без свопа (как у postgres/api выше), прод-размеры поднимаются
# этой переменной.
variable "observability_memory_mb" {
  type = object({
    collector  = number
    loki       = number
    tempo      = number
    prometheus = number
    grafana    = number
    # node_exporter — статический сборщик с фиксированным набором collectors:
    # десятков мегабайт хватает. socket-proxy — haproxy над демоном Docker:
    # несколько соединений receiver docker_stats коллектора.
    node_exporter = number
    docker_proxy  = number
  })
  description = "Потолки памяти контейнеров стека наблюдаемости, МБ, без свопа."
  default = {
    collector     = 384
    loki          = 768
    tempo         = 768
    prometheus    = 768
    grafana       = 512
    node_exporter = 64
    docker_proxy  = 64
  }
}

variable "grafana_port" {
  type        = number
  description = "Порт Grafana на bind_ip: наружу не публикуется, в UI заходят через SSH-туннель."
  default     = 3000
}

# Пустой по умолчанию: локальный apply без стека не требует секрета. При
# observability_enabled = true валидация роняет apply — на стенде анонимный
# вход выключен, без учётки Grafana не открыть.
variable "grafana_admin_password" {
  type        = string
  description = "Пароль админа Grafana (GF_SECURITY_ADMIN_PASSWORD). Не коммитить."
  default     = ""
  sensitive   = true

  validation {
    condition     = !var.observability_enabled || length(var.grafana_admin_password) > 0
    error_message = "grafana_admin_password обязателен при observability_enabled = true: анонимный вход в Grafana на стенде выключен (секрет GRAFANA_ADMIN_PASSWORD_DMC268_T1)."
  }
}

# Дефолт — локальные реквизиты compose и дефолт миграции 0013: локальный apply
# поднимается без секрета. Допустим он только при выключенном стеке — при
# observability_enabled = true валидация роняет apply: роль dmc268_monitor
# становится входом в базу, открытым в docker-сети.
variable "postgres_monitor_password" {
  type        = string
  description = "Пароль роли мониторинга базы dmc268_monitor (pg_monitor). Не коммитить."
  default     = "dmc268_monitor"
  sensitive   = true

  validation {
    condition     = length(var.postgres_monitor_password) > 0
    error_message = "postgres_monitor_password не должен быть пустым: миграция 0013 задаёт его роли dmc268_monitor, под ней коллектор снимает метрики базы."
  }

  validation {
    condition     = !var.observability_enabled || var.postgres_monitor_password != "dmc268_monitor"
    error_message = "Дефолт dmc268_monitor допустим только при observability_enabled = false: задайте настоящий пароль (секрет PG_MONITOR_PASSWORD_DMC268_T1)."
  }
}

variable "ollama_base_url" {
  type        = string
  description = "Адрес Ollama (OLLAMA_BASE_URL). Контейнер Ollama подключается к сети dmc268-backend — воркер ходит к нему оттуда; к dmc268-egress — только если Ollama сама скачивает модели."
  default     = "http://dmc268-ollama:11434"
}

# Та же проверка есть в настройках приложения (app/config.py), но там она
# срабатывает только на старте воркера — после того как apply пересоздал
# контейнеры. Здесь — на plan, пока на сервере ничего не изменилось.
variable "ollama_model" {
  type        = string
  description = "Модель Ollama (OLLAMA_MODEL); обязательна при llm_backend = ollama."
  default     = ""

  validation {
    condition     = var.llm_backend != "ollama" || var.ollama_model != ""
    error_message = "llm_backend = ollama требует ollama_model."
  }
}

# Зеркало проверки в app/config.py — по той же причине, что у ollama_model.
variable "llm_hosted_fallback" {
  type        = bool
  description = "Подключить Eurorouter фоллбэком после локальной модели (LLM_HOSTED_FALLBACK). Включение отправляет код проверяемых репозиториев hosted-провайдеру."
  default     = false

  validation {
    condition = !var.llm_hosted_fallback || (
      var.llm_backend == "ollama"
      && var.eurorouter_base_url != ""
      && var.eurorouter_model != ""
      && var.eurorouter_api_keys != ""
    )
    error_message = "llm_hosted_fallback = true требует llm_backend = ollama и непустых eurorouter_base_url, eurorouter_model, eurorouter_api_keys."
  }
}

variable "eurorouter_base_url" {
  type        = string
  description = "Адрес OpenAI-совместимого API Eurorouter (EUROROUTER_BASE_URL)."
  default     = ""
}

variable "eurorouter_model" {
  type        = string
  description = "Модель Eurorouter (EUROROUTER_MODEL)."
  default     = ""
}

variable "eurorouter_api_keys" {
  type        = string
  description = "Ключи Eurorouter через запятую (EUROROUTER_API_KEYS). Не коммитить."
  default     = ""
  sensitive   = true
}

# --- Образ API ---

# Digest обязателен: тег перезаписывается, а digest называет ровно тот образ,
# который собрал и проверил этот прогон деплоя. Тег оставлен для читаемости
# лога и summary деплоя (commit sha); контейнеры ссылаются на образ без тега —
# local.api_image_ref в app.tf.
variable "api_image" {
  type        = string
  description = "Ссылка на образ API в реестре с digest: <репозиторий>:<commit sha>@sha256:<digest>."

  validation {
    condition     = can(regex("@sha256:[0-9a-f]{64}$", var.api_image))
    error_message = "api_image должен заканчиваться на @sha256:<64 hex>: образ забирается по digest, а не по тегу."
  }
}
