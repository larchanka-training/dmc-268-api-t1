# Стек наблюдаемости в плане (спека backend-delivery, «Каждый контейнер стека
# SHALL иметь потолок памяти и SHALL ограничивать объём своих логов на диске»):
# остальные tftest гоняют конфигурацию с observability_enabled = false, у всех
# ресурсов стека count = 0, и защита его восьми контейнеров вместе с
# валидациями секретов оставалась бы непроверенной — правка observability.tf
# не вскрылась бы ни одним тестом (замечание ревью PR #130, issue #140). Без
# Docker: mock-провайдер, только plan. Запуск: `cd infra && tofu init
# -backend=false && tofu test`.

mock_provider "docker" {}

variables {
  api_image                 = "ghcr.io/larchanka-training/dmc-268-api-t1:0123abc@sha256:2e0b81d60597f449baa56dc25d355367869d2dca4cd57e866f1e2b52af3eb55f"
  postgres_password         = "p@ss/w:rd"
  rabbitmq_password         = "r@bbit#1"
  github_webhook_secret     = "webhook-secret"
  user_token_encryption_key = "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA="
  llm_backend               = "stub"
}

run "observability_stack_plans_protected" {
  command = plan

  # Валидные секреты включённого стека: пароль админа Grafana непустой, пароль
  # роли мониторинга — не дефолт dmc268_monitor. Только такой набор проходит
  # валидации variables.tf (см. следующий run про невалидный).
  variables {
    observability_enabled     = true
    grafana_admin_password    = "grafana-admin"
    postgres_monitor_password = "monitor-password"
  }

  # Ресурсы стека считаются (count = 1 : 0), поэтому каждый адресуется по
  # индексу: с включённым флагом все восемь есть в плане. Список — по
  # docker_container.* из observability.tf; новый контейнер стека обязан
  # добавляться сюда.
  assert {
    condition = alltrue([
      for c in [
        docker_container.otelcol[0], docker_container.loki[0],
        docker_container.tempo[0], docker_container.prometheus[0],
        docker_container.grafana[0], docker_container.node_exporter[0],
        docker_container.cadvisor[0], docker_container.docker_socket_proxy[0],
      ] :
      c.memory > 0 && c.memory == c.memory_swap
    ])
    error_message = "У каждого контейнера стека должен быть потолок памяти, и memory_swap равен memory: иначе контейнер уйдёт в swap, а не завершится по OOM."
  }

  # Ротация — ровно local.log_opts (locals.tf): литерал в тесте разъезжался бы
  # с ним при смене размеров.
  assert {
    condition = alltrue([
      for c in [
        docker_container.otelcol[0], docker_container.loki[0],
        docker_container.tempo[0], docker_container.prometheus[0],
        docker_container.grafana[0], docker_container.node_exporter[0],
        docker_container.cadvisor[0], docker_container.docker_socket_proxy[0],
      ] :
      c.log_driver == "json-file" && jsonencode(c.log_opts) == jsonencode(local.log_opts)
    ])
    error_message = "У каждого контейнера стека должна быть ротация логов: log_driver json-file и log_opts из local.log_opts (max-size 10m, max-file 3)."
  }

  # contains, а не равенство набора (как у postgres и rabbitmq выше по стилю
  # hardening.tftest.hcl): у контейнера стека могут появиться свои опции,
  # проверяется нижняя планка — запрет повышения привилегий.
  assert {
    condition = alltrue([
      for c in [
        docker_container.otelcol[0], docker_container.loki[0],
        docker_container.tempo[0], docker_container.prometheus[0],
        docker_container.grafana[0], docker_container.node_exporter[0],
        docker_container.cadvisor[0], docker_container.docker_socket_proxy[0],
      ] :
      contains(c.security_opts, "no-new-privileges:true")
    ])
    error_message = "Каждый контейнер стека должен запрещать повышение привилегий: security_opts содержит no-new-privileges:true."
  }
}

# Стек включён, секреты не заданы: незаданный секрет репозитория приходит в
# TF_VAR_* пустой строкой, а пароль роли мониторинга остаётся дефолтом. Обе
# валидации variables.tf обязаны уронить план: анонимный вход в Grafana на
# стенде выключен, а дефолтный пароль dmc268_monitor — открытый вход в базу
# из docker-сети.
run "observability_stack_without_secrets_rejected" {
  command = plan

  variables {
    observability_enabled     = true
    grafana_admin_password    = ""
    postgres_monitor_password = "dmc268_monitor"
  }

  expect_failures = [var.grafana_admin_password, var.postgres_monitor_password]
}
