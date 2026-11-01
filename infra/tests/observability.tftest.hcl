# Стек наблюдаемости в плане (спека backend-delivery, «Каждый контейнер стека
# SHALL иметь потолок памяти и SHALL ограничивать объём своих логов на диске»):
# остальные tftest гоняют конфигурацию с observability_enabled = false, у всех
# ресурсов стека count = 0, и защита его девяти контейнеров вместе с
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
  # Доставка алертов: значения общие для всех run; пустые и битые — в
  # отдельных run ниже.
  alert_telegram_bot_token = "123456789:AAEexampleexampleexampleexample_-12"
  alert_telegram_chat_id   = "-1001234567890"
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
  # индексу: с включённым флагом все двенадцать есть в плане. Список — по
  # docker_container.* из observability.tf; новый контейнер стека обязан
  # добавляться сюда.
  assert {
    condition = alltrue([
      for c in [
        docker_container.otelcol[0], docker_container.otelcol_docker[0],
        docker_container.otelcol_logs[0], docker_container.loki[0],
        docker_container.tempo[0], docker_container.prometheus[0],
        docker_container.grafana[0], docker_container.node_exporter[0],
        docker_container.docker_socket_proxy[0], docker_container.alertmanager[0],
        docker_container.blackbox[0], docker_container.blackbox_public[0],
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
        docker_container.otelcol[0], docker_container.otelcol_docker[0],
        docker_container.otelcol_logs[0], docker_container.loki[0],
        docker_container.tempo[0], docker_container.prometheus[0],
        docker_container.grafana[0], docker_container.node_exporter[0],
        docker_container.docker_socket_proxy[0], docker_container.alertmanager[0],
        docker_container.blackbox[0], docker_container.blackbox_public[0],
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
        docker_container.otelcol[0], docker_container.otelcol_docker[0],
        docker_container.otelcol_logs[0], docker_container.loki[0],
        docker_container.tempo[0], docker_container.prometheus[0],
        docker_container.grafana[0], docker_container.node_exporter[0],
        docker_container.docker_socket_proxy[0], docker_container.alertmanager[0],
        docker_container.blackbox[0], docker_container.blackbox_public[0],
      ] :
      contains(c.security_opts, "no-new-privileges:true")
    ])
    error_message = "Каждый контейнер стека должен запрещать повышение привилегий: security_opts содержит no-new-privileges:true."
  }

  # Хранилища стека сторож не держит: правка конфига — их замена. Окно
  # штатной остановки, чтобы WAL и буферы сбрасывались, а не обрывались SIGKILL.
  assert {
    condition = alltrue([
      for c in [
        docker_container.loki[0], docker_container.tempo[0],
        docker_container.prometheus[0], docker_container.grafana[0],
      ] :
      c.stop_timeout == 30 && c.destroy_grace_seconds == 30
    ])
    error_message = "Loki, Tempo, Prometheus и Grafana должны останавливаться с окном 30 с: stop_timeout = destroy_grace_seconds = 30, иначе замена — SIGKILL под пишущим хранилищем."
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

# Секреты Telegram битые: опечатка всплыла бы только на первом алерте —
# план падает до изменений на сервере.
run "observability_stack_with_broken_telegram_rejected" {
  command = plan

  variables {
    observability_enabled     = true
    grafana_admin_password    = "grafana-admin"
    postgres_monitor_password = "monitor-password"
    alert_telegram_bot_token  = "not-a-token"
    alert_telegram_chat_id    = "@channel"
  }

  expect_failures = [var.alert_telegram_bot_token, var.alert_telegram_chat_id]
}

# Секретов Telegram нет совсем (незаданный секрет приходит пустой строкой):
# стек поднимается, Alertmanager получает заглушки unset и 0 — алерты
# считаются, но не доставляются.
run "observability_stack_without_telegram_plans" {
  command = plan

  variables {
    observability_enabled     = true
    grafana_admin_password    = "grafana-admin"
    postgres_monitor_password = "monitor-password"
    alert_telegram_bot_token  = ""
    alert_telegram_chat_id    = ""
  }

  assert {
    condition = (
      one([for u in docker_container.alertmanager[0].upload : u.content if u.file == "/etc/alertmanager/secrets/telegram_bot_token"]) == "unset"
      && one([for u in docker_container.alertmanager[0].upload : u.content if u.file == "/etc/alertmanager/secrets/telegram_chat_id"]) == "0"
    )
    error_message = "Без секретов Telegram Alertmanager получает заглушки — токен unset и chat id 0, как локально в compose: пустой chat_id_file он не разобрал бы как число."
  }
}

# Секреты Telegram и цель пробы публичного входа доходят до контейнеров файлами.
run "alerting_gets_secrets_as_files_and_the_public_target" {
  command = plan

  variables {
    observability_enabled     = true
    grafana_admin_password    = "grafana-admin"
    postgres_monitor_password = "monitor-password"
    public_origin             = "https://203.0.113.10"
  }

  assert {
    condition = (
      one([for u in docker_container.alertmanager[0].upload : u.content if u.file == "/etc/alertmanager/secrets/telegram_bot_token"]) == var.alert_telegram_bot_token
      && one([for u in docker_container.alertmanager[0].upload : u.content if u.file == "/etc/alertmanager/secrets/telegram_chat_id"]) == var.alert_telegram_chat_id
      && length(coalesce(docker_container.alertmanager[0].env, [])) == 0
    )
    error_message = "Токен бота и chat id попадают в Alertmanager файлами под /etc/alertmanager/secrets (bot_token_file и chat_id_file), а не через env: env виден в docker inspect."
  }

  assert {
    condition     = yamldecode(one([for u in docker_container.prometheus[0].upload : u.content if u.file == "/etc/prometheus/targets/public.yml"])) == [{ targets = ["https://203.0.113.10/"] }]
    error_message = "Цель пробы публичного входа — public_origin со слешем на конце (job blackbox-public, файл /etc/prometheus/targets/public.yml)."
  }
}

# Сети и порт стека по плану (то же сверяет tests/test_infra_isolation.py по
# тексту): collector — в данных, в стеке и в dmc268-otlp, collector-docker —
# только в стеке и в сети socket-proxy, prometheus — в данных и в стеке,
# grafana и коллектор логов — в стеке и в сети публикации своего порта; сети
# стека и dmc268-otlp internal, сети портов — нет; порт Grafana публикуется на
# bind_ip, порт коллектора логов — на 127.0.0.1, куда шлёт драйвер fluentd.
run "stack_networks_and_grafana_port_follow_their_purpose" {
  command = plan

  variables {
    observability_enabled     = true
    grafana_admin_password    = "grafana-admin"
    postgres_monitor_password = "monitor-password"
  }

  assert {
    condition = toset([for n in docker_container.otelcol[0].networks_advanced : n.name]) == toset([
      docker_network.backend.name, docker_network.observability[0].name, docker_network.otlp.name,
    ])
    error_message = "Коллектор должен стоять ровно в трёх сетях: данных (receiver postgres и приём OTLP сервисов), стека (экспортёры, скрейп collector) и dmc268-otlp (трейсы браузера через nginx интерфейса). В сети socket-proxy его быть не должно: inspect отдаёт Env всех контейнеров."
  }

  assert {
    condition = toset([for n in docker_container.otelcol_docker[0].networks_advanced : n.name]) == toset([
      docker_network.observability[0].name, docker_network.observability_docker_proxy[0].name,
    ])
    error_message = "Экземпляр метрик контейнеров должен стоять ровно в двух сетях: стека (скрейп collector-docker) и socket-proxy (receiver docker_stats). Без сети данных и dmc268-otlp: OTLP он не принимает."
  }

  assert {
    condition = toset([for n in docker_container.otelcol_logs[0].networks_advanced : n.name]) == toset([
      docker_network.observability[0].name, docker_network.logs[0].name,
    ]) && docker_network.logs[0].internal != true
    error_message = "Коллектор логов должен стоять ровно в двух сетях: стека (отдача основному коллектору) и dmc268-logs — не internal, там публикуется его порт. Без сетей данных и socket-proxy."
  }

  assert {
    condition = (
      length(docker_container.otelcol_logs[0].ports) == 1
      && docker_container.otelcol_logs[0].ports[0].ip == "127.0.0.1"
      && local.fluentd_log_opts["fluentd-address"] == "tcp://127.0.0.1:${docker_container.otelcol_logs[0].ports[0].external}"
    )
    error_message = "Коллектор логов публикует ровно один порт — на 127.0.0.1, и драйвер fluentd шлёт именно туда (local.fluentd_log_opts)."
  }

  assert {
    condition = toset([for n in docker_container.prometheus[0].networks_advanced : n.name]) == toset([
      docker_network.backend.name, docker_network.observability[0].name,
    ])
    error_message = "Prometheus должен стоять ровно в двух сетях: данных (скрейп rabbitmq) и стека (скрейп collector, collector-docker и node-exporter)."
  }

  assert {
    condition = toset([for n in docker_container.alertmanager[0].networks_advanced : n.name]) == toset([
      docker_network.observability[0].name, docker_network.alerting[0].name,
    ]) && docker_network.alerting[0].internal != true && length(docker_container.alertmanager[0].ports) == 0
    error_message = "Alertmanager стоит ровно в двух сетях: стека (приём алертов от Prometheus) и dmc268-alerting — не internal, выход в api.telegram.org. Портов на хосте у него нет."
  }

  assert {
    condition = toset([for n in docker_container.blackbox[0].networks_advanced : n.name]) == toset([
      docker_network.observability[0].name, docker_network.backend.name,
    ]) && length(docker_container.blackbox[0].ports) == 0
    error_message = "blackbox с пробами сервисов стоит ровно в сетях стека и данных, без выхода наружу: /probe без авторизации сделал бы его прокси в интернет для сети данных. Портов на хосте у него нет."
  }

  assert {
    condition = toset([for n in docker_container.blackbox_public[0].networks_advanced : n.name]) == toset([
      docker_network.observability[0].name, docker_network.alerting[0].name,
    ]) && length(docker_container.blackbox_public[0].ports) == 0
    error_message = "blackbox-public стоит ровно в сетях стека и dmc268-alerting (проба публичного входа), без сети данных. Портов на хосте у него нет."
  }

  assert {
    condition     = docker_network.otlp.internal == true
    error_message = "Сеть dmc268-otlp — internal: через неё интерфейс ходит в коллектор, выхода в интернет у коллектора быть не должно."
  }

  assert {
    condition = toset([for n in docker_container.grafana[0].networks_advanced : n.name]) == toset([
      docker_network.observability[0].name, docker_network.grafana[0].name,
    ])
    error_message = "Grafana должна стоять в сети стека (datasources loki/tempo/prometheus) и в отдельной сети dmc268-grafana, где публикуется её порт."
  }

  assert {
    condition     = docker_network.observability[0].internal == true && docker_network.grafana[0].internal != true
    error_message = "Сеть стека — internal (из неё в интернет не выйти), сеть dmc268-grafana — не internal: порты на internal-сети Docker не публикует."
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

# Логи базы и брокера — в коллектор логов только со стеком: без него
# остаётся json-file с ротацией (hardening.tftest.hcl).
run "container_logs_go_to_the_logs_collector_with_the_stack" {
  command = plan

  variables {
    observability_enabled     = true
    grafana_admin_password    = "grafana-admin"
    postgres_monitor_password = "monitor-password"
  }

  assert {
    condition = alltrue([
      for c in [docker_container.postgres, docker_container.postgres_replica, docker_container.rabbitmq] :
      c.log_driver == "fluentd" && jsonencode(c.log_opts) == jsonencode(local.fluentd_log_opts)
    ])
    error_message = "Со стеком postgres, его реплика и rabbitmq пишут логи драйвером fluentd с local.fluentd_log_opts."
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

run "otlp_network_exists_without_the_stack" {
  command = plan

  # observability_enabled по умолчанию false. К dmc268-otlp по имени подключается
  # стек dmc-268-ui-t1: без сети его plan упал бы на data source.
  assert {
    condition     = docker_network.otlp.name == "dmc268-otlp"
    error_message = "Сеть dmc268-otlp должна создаваться и с выключенным стеком: на неё по имени ссылается стек интерфейса."
  }
}
