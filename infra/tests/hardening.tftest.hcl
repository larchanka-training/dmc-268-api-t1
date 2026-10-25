# Защита контейнеров стенда (спека backend-delivery, «Контейнеры стека работают
# с минимальными привилегиями»): правка одного контейнера не должна тихо
# снимать защиту. Без Docker: mock-провайдер, только plan. Запуск:
# `cd infra && tofu init -backend=false && tofu test`. Миграции идут шагом
# деплоя, не контейнером стека: их ограничения — флаги `docker run` в
# .github/scripts/deploy/migrate.sh, их сверяет tests/test_stack_descriptions.py.

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

run "third_party_containers_forbid_privilege_escalation" {
  command = plan

  # Их entrypoint стартует от root (chown каталога данных) и переходит на своего
  # пользователя, поэтому read_only и drop ALL сейчас не включены. Отсутствие
  # защиты не закреплено: усилить её можно, тест проверяет только нижнюю планку.
  assert {
    condition = alltrue([
      for c in [docker_container.postgres, docker_container.rabbitmq, docker_container.postgres_replica] :
      toset(c.security_opts) == toset(["no-new-privileges:true"])
    ])
    error_message = "postgres, rabbitmq и реплика должны запрещать повышение привилегий: security_opts = [\"no-new-privileges:true\"]."
  }
}

run "app_containers_run_with_minimal_privileges" {
  command = plan

  assert {
    condition = alltrue([
      for c in [docker_container.api, docker_container.worker] :
      c.read_only == true
    ])
    error_message = "api и worker должны работать с файловой системой только для чтения."
  }

  assert {
    condition = alltrue([
      for c in [docker_container.api, docker_container.worker] :
      jsonencode(c.tmpfs) == jsonencode({ "/tmp" = "rw,noexec,nosuid,size=64m" })
    ])
    error_message = "api и worker пишут только в /tmp: tmpfs rw,noexec,nosuid,size=64m."
  }

  assert {
    condition = alltrue([
      for c in [docker_container.api, docker_container.worker] :
      length(c.capabilities) == 1 && toset(one(c.capabilities).drop) == toset(["ALL"]) && length(coalesce(one(c.capabilities).add, toset([]))) == 0
    ])
    error_message = "api и worker должны сбрасывать все Linux capabilities (drop = [\"ALL\"]) и ничего не добавлять."
  }

  assert {
    condition = alltrue([
      for c in [docker_container.api, docker_container.worker] :
      toset(c.security_opts) == toset(["no-new-privileges:true"])
    ])
    error_message = "api и worker должны запрещать повышение привилегий: security_opts = [\"no-new-privileges:true\"]."
  }

  assert {
    condition = alltrue([
      for c in [docker_container.api, docker_container.worker] :
      c.init == true
    ])
    error_message = "api и worker должны запускаться с init: без него некому подбирать зомби-процессы и передавать сигналы."
  }
}

run "replica_helpers_run_with_minimal_privileges" {
  command = plan

  # Настройка роли и бэкап — свои скрипты на образе postgres: файловая система
  # только для чтения (пишут лишь в /tmp и в том копий), без capabilities.
  assert {
    condition = alltrue([
      for c in [docker_container.replication_setup, docker_container.postgres_backup] :
      c.read_only == true
      && jsonencode(c.tmpfs) == jsonencode({ "/tmp" = "rw,noexec,nosuid,size=16m" })
      && length(c.capabilities) == 1 && toset(one(c.capabilities).drop) == toset(["ALL"]) && length(coalesce(one(c.capabilities).add, toset([]))) == 0
      && toset(c.security_opts) == toset(["no-new-privileges:true"])
    ])
    error_message = "replication_setup и postgres_backup: read_only, tmpfs /tmp 16m, drop ALL, no-new-privileges."
  }

  assert {
    condition     = docker_container.postgres_backup.init == true && docker_container.postgres_replica.init == true
    error_message = "Бэкап и реплика запускаются с init: sh как PID 1 игнорирует SIGTERM."
  }
}

run "every_container_has_memory_ceiling_and_log_rotation" {
  command = plan

  assert {
    condition = alltrue([
      for c in [
        docker_container.postgres, docker_container.rabbitmq,
        docker_container.api, docker_container.worker,
        docker_container.replication_setup, docker_container.postgres_replica, docker_container.postgres_backup,
      ] :
      c.memory > 0 && c.memory == c.memory_swap
    ])
    error_message = "У каждого контейнера должен быть потолок памяти, и memory_swap равен memory: иначе контейнер уйдёт в swap, а не завершится по OOM."
  }

  assert {
    condition = alltrue([
      for c in [
        docker_container.postgres, docker_container.rabbitmq,
        docker_container.api, docker_container.worker,
        docker_container.replication_setup, docker_container.postgres_replica, docker_container.postgres_backup,
      ] :
      c.log_driver == "json-file" && jsonencode(c.log_opts) == jsonencode({ "max-size" = "10m", "max-file" = "3" })
    ])
    error_message = "У каждого контейнера должна быть ротация логов: log_driver json-file, max-size 10m, max-file 3."
  }
}
