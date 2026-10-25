# Логика infra/main.tf и проверки переменных без Docker и без сервера:
# mock-провайдер не ходит к демону, вычисляемые атрибуты он заполняет сам
# (строки — случайные, числа — 0). Запуск: `cd infra && tofu init -backend=false
# && tofu test`.

mock_provider "docker" {}

# Минимальный набор, с которым конфигурация проходит plan. Пароли со
# спецсимволами — чтобы проверка адресов ловила пропущенный urlencode.
variables {
  api_image                 = "ghcr.io/larchanka-training/dmc-268-api-t1:0123abc@sha256:2e0b81d60597f449baa56dc25d355367869d2dca4cd57e866f1e2b52af3eb55f"
  postgres_password         = "p@ss/w:rd"
  rabbitmq_password         = "r@bbit#1"
  github_webhook_secret     = "webhook-secret"
  user_token_encryption_key = "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA="
  llm_backend               = "stub"
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

  assert {
    condition = alltrue([
      for c in [docker_container.postgres, docker_container.rabbitmq, docker_container.migrate, docker_container.api, docker_container.worker] :
      [for n in c.networks_advanced : n.name] == [docker_network.dmc268.name]
    ])
    error_message = "Все контейнеры стека — в одной сети dmc268: по ней приложение находит базу и брокер по имени."
  }

  assert {
    condition     = length(docker_container.worker.ports) == 0 && length(docker_container.migrate.ports) == 0
    error_message = "worker и migrate не публикуют портов."
  }

  assert {
    condition = (
      docker_container.api.ports[0].ip == var.bind_ip
      && docker_container.postgres.ports[0].ip == var.internal_bind_ip
      && docker_container.rabbitmq.ports[0].ip == var.internal_bind_ip
    )
    error_message = "API публикуется на bind_ip, PostgreSQL и RabbitMQ — на internal_bind_ip."
  }

  # Проверка живости ходит на тот порт, который слушает API внутри контейнера.
  assert {
    condition     = strcontains(docker_container.api.healthcheck[0].test[1], "127.0.0.1:${docker_container.api.ports[0].internal}/health")
    error_message = "healthcheck API должен опрашивать /health на внутреннем порту API."
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
    bind_ip          = "0.0.0.0"
    internal_bind_ip = "10.0.0.5"
  }

  expect_failures = [var.bind_ip, var.internal_bind_ip]
}

# Незаданный секрет в CI приходит в TF_VAR_* пустой строкой.
run "empty_secrets_rejected" {
  command = plan

  variables {
    github_webhook_secret = ""
    postgres_password     = ""
    rabbitmq_password     = ""
  }

  expect_failures = [var.github_webhook_secret, var.postgres_password, var.rabbitmq_password]
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
