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

variable "postgres_password" {
  type        = string
  description = "PostgreSQL password. Do not commit real values."
  sensitive   = true
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

variable "ollama_model" {
  type        = string
  description = "Модель Ollama (OLLAMA_MODEL); обязательна при llm_backend = ollama."
  default     = ""
}

variable "llm_hosted_fallback" {
  type        = bool
  description = "Подключить Eurorouter фоллбэком после локальной модели (LLM_HOSTED_FALLBACK). Включение отправляет код проверяемых репозиториев hosted-провайдеру."
  default     = false
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
}

variable "api_image" {
  type        = string
  description = "Полная ссылка на образ API в реестре, с тегом по commit sha."
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
