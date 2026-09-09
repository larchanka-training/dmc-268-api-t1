variable "docker_host" {
  type        = string
  description = "Docker daemon URI. Use unix:///var/run/docker.sock locally or ssh://user@server for a remote host."
  default     = "unix:///var/run/docker.sock"
}

variable "bind_ip" {
  type        = string
  description = "Interface to publish container ports on. Not used as a client hostname."
  default     = "127.0.0.1"
}

variable "service_host" {
  type        = string
  description = "Hostname or IP clients use in connection URLs (must be reachable, not 0.0.0.0)."
  default     = "127.0.0.1"
}

variable "python_version" {
  type        = string
  description = "Python version for the API image."
  default     = "3.14"
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

variable "redis_port" {
  type        = number
  description = "Host port for Redis."
  default     = 6379
}

variable "redis_password" {
  type        = string
  description = "Redis password. Do not commit real values."
  sensitive   = true
}
