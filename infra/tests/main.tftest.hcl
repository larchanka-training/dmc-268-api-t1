# Логика infra/*.tf и проверки переменных без Docker и без сервера:
# mock-провайдер не ходит к демону, вычисляемые атрибуты он заполняет сам
# (строки — случайные, числа — 0). Запуск: `cd infra && tofu init -backend=false
# && tofu test`.

mock_provider "docker" {}

# Минимальный набор, с которым конфигурация проходит plan. Пароли со
# спецсимволами — чтобы проверка адресов ловила пропущенный urlencode.
variables {
  api_image                     = "ghcr.io/larchanka-training/dmc-268-api-t1:0123abc@sha256:2e0b81d60597f449baa56dc25d355367869d2dca4cd57e866f1e2b52af3eb55f"
  postgres_password             = "p@ss/w:rd"
  rabbitmq_password             = "r@bbit#1"
  postgres_replication_password = "repl:ication@pass-16"
  github_webhook_secret         = "webhook-secret"
  user_token_encryption_key     = "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA="
  llm_backend                   = "stub"
}

run "app_containers_get_connection_settings" {
  command = plan

  # Хост в адресах — имя контейнера в общей сети: переименование контейнера
  # без правки адреса оставило бы приложение без базы и брокера.
  assert {
    condition = alltrue([
      for c in [docker_container.migrate, docker_container.api, docker_container.worker] :
      contains(c.env, "DATABASE_URL=postgresql+psycopg://dmc268:p%40ss%2Fw%3Ard@${docker_container.postgres.name}:5432/dmc268")
    ])
    error_message = "migrate, api и worker должны получать DATABASE_URL с закодированным паролем и именем контейнера postgres."
  }

  assert {
    condition = alltrue([
      for c in [docker_container.migrate, docker_container.api, docker_container.worker] :
      contains(c.env, "RABBITMQ_URL=amqp://dmc268:r%40bbit%231@${docker_container.rabbitmq.name}:5672/%2F")
    ])
    error_message = "migrate, api и worker должны получать RABBITMQ_URL с закодированным паролем и vhost %2F."
  }

  assert {
    condition = alltrue([
      for c in [docker_container.migrate, docker_container.api, docker_container.worker] :
      contains(c.env, "GITHUB_WEBHOOK_SECRET=webhook-secret")
    ])
    error_message = "Без GITHUB_WEBHOOK_SECRET общие настройки приложения не загрузятся."
  }
}

run "secrets_reach_only_their_containers" {
  command = plan

  # Ключ шифрования токенов и client secret нужны только входу в API.
  assert {
    condition = alltrue([
      for c in [docker_container.migrate, docker_container.worker] : length([
        for e in c.env : e
        if startswith(e, "USER_TOKEN_ENCRYPTION_KEY=") || startswith(e, "GITHUB_APP_CLIENT_SECRET=")
      ]) == 0
    ])
    error_message = "USER_TOKEN_ENCRYPTION_KEY и GITHUB_APP_CLIENT_SECRET не должны попадать в migrate и worker."
  }

  assert {
    condition = (
      contains(docker_container.api.env, "USER_TOKEN_ENCRYPTION_KEY=${var.user_token_encryption_key}")
      && contains(docker_container.worker.env, "LLM_BACKEND=stub")
      && length([for e in docker_container.api.env : e if startswith(e, "LLM_BACKEND=")]) == 0
    )
    error_message = "Ключ токенов — у API, адаптер модели — у воркера, и не наоборот."
  }
}

run "only_api_published_and_only_on_loopback" {
  command = plan

  # Сети по назначению (спека backend-delivery, «Сети стека разделены по
  # назначению»): база, реплика и брокер — только в сети данных, API — во входе
  # и в данных, воркер — в данных и в исходящей. tests/test_infra_isolation.py
  # сверяет то же по тексту; здесь — по плану.
  assert {
    condition = alltrue([
      for c in [
        docker_container.postgres, docker_container.rabbitmq, docker_container.migrate,
        docker_container.replication_setup, docker_container.postgres_replica, docker_container.postgres_backup,
      ] :
      toset([for n in c.networks_advanced : n.name]) == toset([docker_network.backend.name])
    ])
    error_message = "База, реплика, брокер, migrate, настройка роли и бэкап — только в сети данных dmc268-backend."
  }

  assert {
    condition = (
      toset([for n in docker_container.api.networks_advanced : n.name]) == toset([docker_network.dmc268.name, docker_network.backend.name])
      && toset([for n in docker_container.worker.networks_advanced : n.name]) == toset([docker_network.backend.name, docker_network.egress.name])
    )
    error_message = "API — во входе dmc268 и в данных; воркер — в данных и в исходящей dmc268-egress, не во входе."
  }

  assert {
    condition     = docker_network.backend.internal == true && docker_network.dmc268.name == "dmc268"
    error_message = "Сеть данных — internal (без выхода наружу); сеть входа сохраняет имя dmc268 — по нему подключается интерфейс."
  }

  assert {
    condition = alltrue([
      for c in [
        docker_container.postgres, docker_container.rabbitmq, docker_container.migrate, docker_container.worker,
        docker_container.replication_setup, docker_container.postgres_replica, docker_container.postgres_backup,
      ] :
      length(c.ports) == 0
    ])
    error_message = "Портов на хосте нет ни у кого, кроме API: база и брокер — только во внутренней сети."
  }

  assert {
    condition     = docker_container.api.ports[0].ip == var.bind_ip
    error_message = "API публикуется на bind_ip."
  }

  # Проверка живости ходит на тот порт, который слушает API внутри контейнера.
  assert {
    condition     = strcontains(docker_container.api.healthcheck[0].test[1], "127.0.0.1:${docker_container.api.ports[0].internal}/health")
    error_message = "healthcheck API должен опрашивать /health на внутреннем порту API."
  }
}

run "databases_stop_gracefully" {
  command = plan

  # Без destroy_grace_seconds пересоздание — SIGKILL: база поднимается с
  # восстановлением после аварии, брокер теряет несброшенное.
  assert {
    condition = alltrue([
      for c in [docker_container.postgres, docker_container.rabbitmq, docker_container.postgres_replica] :
      c.stop_timeout == 60 && c.destroy_grace_seconds == 60
    ])
    error_message = "postgres, rabbitmq и реплика останавливаются через SIGTERM: stop_timeout и destroy_grace_seconds по 60 с."
  }
}

run "replica_and_backup_find_their_servers" {
  command = plan

  assert {
    condition = (
      contains(docker_container.postgres_replica.env, "PRIMARY_HOST=${docker_container.postgres.name}")
      && contains(docker_container.postgres_backup.env, "PRIMARY_HOST=${docker_container.postgres.name}")
      && contains(docker_container.postgres_backup.env, "REPLICA_HOST=${docker_container.postgres_replica.name}")
      && contains(docker_container.replication_setup.env, "PGHOST=${docker_container.postgres.name}")
    )
    error_message = "Реплика, бэкап и настройка роли находят серверы по именам контейнеров в сети данных."
  }

  assert {
    condition     = contains(docker_container.postgres.command, "max_slot_wal_keep_size=2GB") && contains(docker_container.postgres.command, "hba_file=/etc/postgresql/pg_hba.conf")
    error_message = "Основной сервер ограничивает WAL под слот реплики и читает pg_hba.conf вне каталога данных."
  }
}

run "replication_password_stays_out_of_commands" {
  command = plan

  # Аргументы видны в `ps` на хосте: пароль реплики — только в окружении.
  assert {
    condition = alltrue([
      for c in [docker_container.replication_setup, docker_container.postgres_replica, docker_container.postgres_backup, docker_container.postgres] :
      length([for a in coalesce(c.command, []) : a if strcontains(a, var.postgres_replication_password)]) == 0
    ])
    error_message = "Пароль роли replicator не должен попадать в аргументы контейнеров."
  }

  assert {
    condition     = contains(docker_container.postgres_replica.env, "REPLICATION_PASSWORD=${var.postgres_replication_password}")
    error_message = "Реплика получает пароль окружением и сама пишет из него passfile."
  }
}

run "worker_grace_covers_one_provider" {
  command = plan

  # 2 вызова × 300 с × 2 попытки × 1 провайдер + 60 с.
  assert {
    condition     = docker_container.worker.stop_timeout == 1260 && docker_container.worker.destroy_grace_seconds == 1260
    error_message = "Без фоллбэка воркер дорабатывает сообщение до 1260 с — и при docker stop, и при пересоздании."
  }
}

run "worker_grace_covers_hosted_fallback" {
  command = plan

  variables {
    llm_backend         = "ollama"
    ollama_model        = "qwen3-coder:30b"
    llm_hosted_fallback = true
    eurorouter_base_url = "https://eurorouter.example/v1"
    eurorouter_model    = "model"
    eurorouter_api_keys = "k1,k2"
  }

  assert {
    condition     = docker_container.worker.stop_timeout == 2460 && docker_container.worker.destroy_grace_seconds == 2460
    error_message = "С фоллбэком провайдеров два, и воркер ждёт до 2460 с."
  }

  assert {
    condition     = contains(docker_container.worker.env, "LLM_HOSTED_FALLBACK=true") && contains(docker_container.worker.env, "OLLAMA_MODEL=qwen3-coder:30b")
    error_message = "Выбор модели доходит до воркера окружением."
  }
}

run "app_containers_run_the_api_image" {
  # apply на mock-провайдере: image_id известен только после создания образа.
  command = apply

  assert {
    condition = alltrue([
      for c in [docker_container.migrate, docker_container.api, docker_container.worker] :
      c.image == docker_image.api.image_id
    ])
    error_message = "migrate, api и worker запускаются из одного образа API."
  }

  assert {
    condition     = docker_image.api.name == var.api_image
    error_message = "Образ API забирается ровно по переданной ссылке, с digest."
  }
}

run "failed_migration_stops_deploy" {
  command = plan

  override_resource {
    target = docker_container.migrate
    values = {
      exit_code = 1
    }
  }

  expect_failures = [docker_container.migrate]
}

run "public_bind_addresses_rejected" {
  command = plan

  variables {
    bind_ip = "0.0.0.0"
  }

  expect_failures = [var.bind_ip]
}

# Незаданный секрет в CI приходит в TF_VAR_* пустой строкой.
run "empty_secrets_rejected" {
  command = plan

  variables {
    github_webhook_secret         = ""
    postgres_password             = ""
    rabbitmq_password             = ""
    postgres_replication_password = ""
  }

  expect_failures = [var.github_webhook_secret, var.postgres_password, var.rabbitmq_password, var.postgres_replication_password]
}

run "ollama_without_model_rejected" {
  command = plan

  variables {
    llm_backend  = "ollama"
    ollama_model = ""
  }

  expect_failures = [var.ollama_model]
}

run "hosted_fallback_without_eurorouter_rejected" {
  command = plan

  variables {
    llm_backend         = "ollama"
    ollama_model        = "qwen3-coder:30b"
    llm_hosted_fallback = true
    eurorouter_base_url = "https://eurorouter.example/v1"
    eurorouter_model    = "model"
    eurorouter_api_keys = ""
  }

  expect_failures = [var.llm_hosted_fallback]
}

run "api_image_without_digest_rejected" {
  command = plan

  variables {
    api_image = "ghcr.io/larchanka-training/dmc-268-api-t1:0123abc"
  }

  expect_failures = [var.api_image]
}
