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
  api_image                     = "ghcr.io/larchanka-training/dmc-268-api-t1:0123abc@sha256:2e0b81d60597f449baa56dc25d355367869d2dca4cd57e866f1e2b52af3eb55f"
  postgres_password             = "p@ss/w:rd"
  rabbitmq_password             = "r@bbit#1"
  postgres_replication_password = "repl:ication@pass-16"
  github_webhook_secret         = "webhook-secret"
  user_token_encryption_key     = "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA="
  llm_backend                   = "stub"
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

# Сети и порт стека по плану (то же сверяет tests/test_infra_isolation.py по
# тексту): collector и prometheus — в данных и в стеке, grafana — в стеке и
# в сети публикации порта; сеть стека internal, сеть порта Grafana — нет;
# порт Grafana публикуется на bind_ip.
run "stack_networks_and_grafana_port_follow_their_purpose" {
  command = plan

  variables {
    observability_enabled     = true
    grafana_admin_password    = "grafana-admin"
    postgres_monitor_password = "monitor-password"
  }

  assert {
    condition = alltrue([
      for c in [docker_container.otelcol[0], docker_container.prometheus[0]] :
      toset([for n in c.networks_advanced : n.name]) == toset([
        docker_network.backend.name, docker_network.observability[0].name,
      ])
    ])
    error_message = "Коллектор и Prometheus должны стоять ровно в двух сетях: данных (receiver postgres, скрейп rabbitmq и приём OTLP) и стека (экспортёры, скрейп collector/node-exporter/cadvisor)."
  }

  assert {
    condition = toset([for n in docker_container.grafana[0].networks_advanced : n.name]) == toset([
      docker_network.observability[0].name, docker_network.grafana[0].name,
    ])
    error_message = "Grafana должна стоять в сети стека (datasources loki/tempo/prometheus) и в отдельной сети dmc268-grafana, где публикуется её порт."
  }

  assert {
    condition     = docker_network.observability[0].internal == true && docker_network.grafana[0].internal != true
    error_message = "Сеть стека — internal (наружу из стека не ходит никто), сеть dmc268-grafana — не internal: порты на internal-сети Docker не публикует."
  }

  assert {
    condition = (
      docker_container.grafana[0].ports[0].ip == var.bind_ip
      && docker_container.grafana[0].ports[0].external == var.grafana_port
      && docker_container.grafana[0].ports[0].internal == 3000
    )
    error_message = "Grafana публикует ровно один порт: 3000 из контейнера на grafana_port и bind_ip (петля; снаружи — SSH-туннель)."
  }
}

# Команда базы следует за флагом: preload pg_stat_statements — только вместе
# со стеком, track_utility=off — рядом с preload. Без него утилитные выражения
# (`ALTER ROLE … PASSWORD` из replication-setup.sql и ранбуков) оседают в
# pg_stat_statements открытым текстом, а роль с pg_monitor их читает
# (замечание ревью PR #130, medium).
run "postgres_command_tracks_the_stack_flag" {
  command = plan

  variables {
    observability_enabled     = true
    grafana_admin_password    = "grafana-admin"
    postgres_monitor_password = "monitor-password"
  }

  assert {
    condition = (
      contains(docker_container.postgres.command, "shared_preload_libraries=pg_stat_statements")
      && contains(docker_container.postgres.command, "pg_stat_statements.track_utility=off")
    )
    error_message = "С включённым стеком postgres должен нести preload pg_stat_statements и track_utility=off: пароли из ALTER ROLE не попадают в pg_stat_statements."
  }
}

run "postgres_command_without_the_stack_has_no_preload" {
  command = plan

  # observability_enabled по умолчанию false — preload и track_utility в
  # команде быть не должны: накладные расходы держать не просили.
  assert {
    condition = (
      !contains(docker_container.postgres.command, "shared_preload_libraries=pg_stat_statements")
      && !contains(docker_container.postgres.command, "pg_stat_statements.track_utility=off")
    )
    error_message = "С выключенным стеком команда postgres не должна нести pg_stat_statements ни в каком виде."
  }
}
