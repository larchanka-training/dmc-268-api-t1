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

variable "postgres_replication_password" {
  type        = string
  description = "Пароль роли replicator, под которой реплика забирает поток с основного сервера. Не коммитить."
  sensitive   = true

  # Пустой пароль сделал бы роль без входа, и реплика молча не собралась бы:
  # ловим на plan, пока на сервере ничего не изменилось.
  validation {
    condition     = length(var.postgres_replication_password) >= 16
    error_message = "postgres_replication_password обязателен и не короче 16 символов."
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
  description = "Сколько дней хранить резервные копии базы."
  default     = 7
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
