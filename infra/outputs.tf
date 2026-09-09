output "postgres_url" {
  description = "PostgreSQL connection URL."
  value       = "postgres://${urlencode(var.postgres_user)}:${urlencode(var.postgres_password)}@${var.service_host}:${var.postgres_port}/${var.postgres_db}"
  sensitive   = true
}

output "rabbitmq_url" {
  description = "RabbitMQ AMQP URL."
  value       = "amqp://${urlencode(var.rabbitmq_user)}:${urlencode(var.rabbitmq_password)}@${var.service_host}:${var.rabbitmq_port}//"
  sensitive   = true
}

output "redis_url" {
  description = "Redis connection URL."
  value       = "redis://:${urlencode(var.redis_password)}@${var.service_host}:${var.redis_port}/0"
  sensitive   = true
}

output "api_url" {
  description = "HTTP URL of the FastAPI service (Python ${var.python_version})."
  value       = "http://${var.service_host}:${var.api_port}"
}
