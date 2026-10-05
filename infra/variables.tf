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
  description = "GitHub App id (GITHUB_APP_ID for the API container)."
  default     = ""
}

variable "github_app_private_key" {
  type        = string
  description = "GitHub App private key in PEM (GITHUB_APP_PRIVATE_KEY). Do not commit real values."
  default     = ""
  sensitive   = true
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
