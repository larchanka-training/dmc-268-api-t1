output "postgres_url" {
  description = "PostgreSQL connection URL."
  value       = "postgres://${var.postgres_user}:${var.postgres_password}@${var.bind_ip}:${var.postgres_port}/${var.postgres_db}"
  sensitive   = true
}

output "rabbitmq_url" {
  description = "RabbitMQ AMQP URL."
  value       = "amqp://${var.rabbitmq_user}:${var.rabbitmq_password}@${var.bind_ip}:${var.rabbitmq_port}//"
  sensitive   = true
}

output "redis_url" {
  description = "Redis connection URL."
  value       = "redis://:${var.redis_password}@${var.bind_ip}:${var.redis_port}/0"
  sensitive   = true
}
