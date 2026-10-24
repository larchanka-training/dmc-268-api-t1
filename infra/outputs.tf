# URL базы и брокера наружу больше не отдаются: портов на хосте у них нет,
# они доступны только из внутренней сети стека (dmc268-backend).

output "api_url" {
  description = "HTTP URL of the FastAPI service."
  value       = "http://${var.service_host}:${var.api_port}"
}
