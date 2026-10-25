variable "docker_host" {
  type        = string
  description = "Docker daemon URI. Use unix:///var/run/docker.sock locally or ssh://user@server for a remote host."
  default     = "unix:///var/run/docker.sock"
}

variable "bind_ip" {
  type        = string
  description = "Interface to publish the API port on. Not used as a client hostname."
  default     = "127.0.0.1"

  # Спека backend-delivery: порт API не выходит за пределы сервера. Гарантирует
  # это только петля; ловим ошибку на plan, пока на сервере ничего не изменилось.
  validation {
    condition     = can(regex("^127\\.", var.bind_ip)) || var.bind_ip == "::1"
    error_message = "bind_ip должен быть адресом петли (127.0.0.0/8 или ::1): иначе порт API окажется доступен снаружи."
  }
}

variable "internal_bind_ip" {
  type        = string
  description = "Interface to publish PostgreSQL and RabbitMQ on the host (keep 127.0.0.1 unless you need local tools only)."
  default     = "127.0.0.1"

  # Спека backend-delivery: порты PostgreSQL и RabbitMQ не выходят за пределы
  # сервера. Гарантирует это только петля; ловим ошибку на plan, пока на сервере
  # ничего не изменилось.
  validation {
    condition     = can(regex("^127\\.", var.internal_bind_ip)) || var.internal_bind_ip == "::1"
    error_message = "internal_bind_ip должен быть адресом петли (127.0.0.0/8 или ::1): иначе порты PostgreSQL и RabbitMQ окажутся доступны снаружи."
  }
}

variable "service_host" {
  type        = string
  description = "Hostname or IP clients use in connection URLs (must be reachable, not 0.0.0.0)."
  default     = "127.0.0.1"
}

variable "api_port" {
  type        = number
  description = "Host port for the FastAPI HTTP server."
  default     = 8000
}

variable "postgres_user" {
  type        = string
  description = "PostgreSQL user name."
  default     = "dmc268"
}

# Незаданный секрет репозитория приходит в TF_VAR_* пустой строкой. Без
# проверки apply пересоздал бы базу и брокер с пустым паролем, а упал бы
# только минутой позже, на миграциях, — уже со сломанным стендом.
variable "postgres_password" {
  type        = string
  description = "PostgreSQL password. Do not commit real values."
  sensitive   = true

  validation {
    condition     = var.postgres_password != ""
    error_message = "postgres_password пуст: секрет PG_PASSWORD_DMC268_T1 не задан."
  }
}

variable "postgres_db" {
  type        = string
  description = "PostgreSQL database name."
  default     = "dmc268"
}

variable "postgres_port" {
  type        = number
  description = "Host port for PostgreSQL."
  default     = 5432
}

variable "rabbitmq_user" {
  type        = string
  description = "RabbitMQ user name."
  default     = "dmc268"
}

variable "rabbitmq_password" {
  type        = string
  description = "RabbitMQ password. Do not commit real values."
  sensitive   = true

  validation {
    condition     = var.rabbitmq_password != ""
    error_message = "rabbitmq_password пуст: секрет RABBITMQ_PASSWORD_DMC268_T1 не задан."
  }
}

variable "rabbitmq_port" {
  type        = number
  description = "Host port for AMQP."
  default     = 5672
}

variable "github_app_id" {
  type        = string
  description = "GitHub App id (GITHUB_APP_ID for the API and worker containers)."
  default     = ""
}

variable "github_app_private_key" {
  type        = string
  description = "GitHub App private key in PEM (GITHUB_APP_PRIVATE_KEY). Do not commit real values."
  default     = ""
  sensitive   = true
}

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

variable "ollama_base_url" {
  type        = string
  description = "Адрес Ollama в сети стека (OLLAMA_BASE_URL)."
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

# Digest обязателен: тег перезаписывается, а digest называет ровно тот образ,
# который собрал и проверил этот прогон деплоя. Тег оставлен для читаемости
# (`docker ps` показывает commit sha).
variable "api_image" {
  type        = string
  description = "Ссылка на образ API в реестре с digest: <репозиторий>:<commit sha>@sha256:<digest>."

  validation {
    condition     = can(regex("@sha256:[0-9a-f]{64}$", var.api_image))
    error_message = "api_image должен заканчиваться на @sha256:<64 hex>: образ забирается по digest, а не по тегу."
  }
}

variable "registry_username" {
  type        = string
  description = "Пользователь реестра образов. В CI — github.actor."
  default     = ""
}

variable "registry_password" {
  type        = string
  description = "Токен реестра образов. В CI — GITHUB_TOKEN, живёт один прогон."
  default     = ""
  sensitive   = true
}
