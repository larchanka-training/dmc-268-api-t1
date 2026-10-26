# Приложение: контейнеры api и worker из образа API в реестре.
# Миграции сюда не входят: их применяет шаг деплоя до `tofu apply`
# (.github/scripts/deploy/migrate.sh) с окружением из выхода migrate_env.

# Образ собирается и публикуется в CI, контейнеры ссылаются на него по digest
# (`api_image` без него не пройдёт проверку). Ресурса docker_image у образа
# API нет: его замена на каждом коммите шла уже после остановки старого API и
# добавляла к простою pull (6 с в деплое #202). Скачивает образ migrate.sh по
# той же ссылке до полного apply, и провайдер создаёт контейнер из готового
# образа: сам он тянет образ, только если того нет на сервере (README.md,
# «Деплой»).
#
# В ссылке контейнера только digest, без тега: атрибут image сравнивается
# строкой, и новый тег при том же digest пересоздавал бы api и worker (в CI тег
# — commit sha, новый на каждом коммите). Тег остаётся в api_image для лога и
# summary деплоя.
locals {
  api_image_ref = join("@", regex("^(.+?)(?::[\\w][\\w.-]{0,127})?@(sha256:[0-9a-f]{64})$", var.api_image))
}

# Ресурсы образа API жили в стеке до #203. Из состояния они уходят без
# уничтожения: keep_locally у них и так не снимал образ с сервера, а старые
# образы убирает шаг чистки деплоя.
removed {
  from = docker_image.api

  lifecycle {
    destroy = false
  }
}

removed {
  from = docker_image.worker

  lifecycle {
    destroy = false
  }
}

# Контейнер миграций жил в стеке до #159. Из состояния он уходит без
# уничтожения: остановленный контейнер на сервере убирает шаг миграций.
removed {
  from = docker_container.migrate

  lifecycle {
    destroy = false
  }
}

resource "docker_container" "api" {
  name    = "dmc268-api"
  image   = local.api_image_ref
  restart = "unless-stopped"

  # Без destroy_grace_seconds провайдер при пересоздании удаляет контейнер
  # принудительно (SIGKILL): lifespan не завершается, спаны не выгружаются.
  # С ним — сначала SIGTERM и ожидание остановки; 10 с — как у `docker stop`.
  # Укладываемся благодаря `--timeout-graceful-shutdown 5` в CMD образа: открытые
  # потоки SSE uvicorn рвёт через 5 с, остальное время — на lifespan.
  destroy_grace_seconds = 10

  # Наш образ: пользователь не root (USER в Dockerfile), приложение ничего не
  # пишет на диск, поэтому файловая система только для чтения и без capabilities.
  # Те же ограничения у миграций — флаги docker run в migrate.sh.
  read_only     = true
  tmpfs         = { "/tmp" = "rw,noexec,nosuid,size=64m" }
  security_opts = ["no-new-privileges:true"]
  init          = true
  memory        = 512
  memory_swap   = 512
  log_driver    = "json-file"
  log_opts      = local.log_opts

  capabilities {
    drop = ["ALL"]
  }

  # Миграции к этому моменту уже применены шагом деплоя.
  depends_on = [
    docker_container.postgres,
    docker_container.rabbitmq,
  ]

  # API — единственный, кто стоит в обеих сетях: запросы приходят из сети
  # входа, база и брокер — в сети данных. Сеть входа не internal: через неё
  # же API ходит в GitHub (OAuth, метаданные PR), и на ней опубликован порт
  # для проверки живости через туннель.
  networks_advanced {
    name = docker_network.dmc268.name
  }

  networks_advanced {
    name = docker_network.backend.name
  }

  # Реквизиты GitHub App: VCS-шлюз у вебхуков (метаданные PR) и у воркера,
  # OAuth и origin — у входа и проверки сессии, ключ токенов пользователя — у
  # входа и ручек репозиториев.
  env = concat(local.app_env, [
    "GITHUB_APP_ID=${var.github_app_id}",
    "GITHUB_APP_PRIVATE_KEY=${var.github_app_private_key}",
    "GITHUB_APP_CLIENT_ID=${var.github_app_client_id}",
    "GITHUB_APP_CLIENT_SECRET=${var.github_app_client_secret}",
    "PUBLIC_ORIGIN=${var.public_origin}",
    # Ключ шифрования токенов пользователя GitHub — только API: воркер их не читает.
    "USER_TOKEN_ENCRYPTION_KEY=${var.user_token_encryption_key}",
  ])

  ports {
    internal = 8000
    external = var.api_port
    ip       = var.bind_ip
  }

  # Apply считает API выкаченным, только когда тот healthy: провайдер после
  # создания контейнера опрашивает его состояние и роняет apply, если healthy
  # не наступил за wait_timeout. Ждёт он только при создании, поэтому
  # повторный деплой без замены API не замедляет. Здоровый API становится
  # healthy за секунды (start_interval ниже); 90 с — запас медленному старту.
  wait         = true
  wait_timeout = 90

  # /health существует именно для этого. Без healthcheck Docker знает только,
  # что процесс не завершился, и зависший uvicorn остался бы живым в его
  # представлении до следующего деплоя. Значения — в local.api_healthcheck.
  healthcheck {
    test           = local.api_healthcheck.test
    interval       = local.api_healthcheck.interval
    timeout        = local.api_healthcheck.timeout
    retries        = local.api_healthcheck.retries
    start_period   = local.api_healthcheck.start_period
    start_interval = local.api_healthcheck.start_interval
  }

  # Правку healthcheck провайдер 4.6.0 планирует обновлением на месте, но
  # Docker не меняет healthcheck запущенного контейнера: diff остаётся вечным,
  # а проверка — старой (проверено на Docker 29.9.2). Поэтому любая правка
  # healthcheck пересоздаёт API.
  lifecycle {
    replace_triggered_by = [terraform_data.api_healthcheck.output]
  }
}

# Healthcheck API отдельным значением: от него пересоздаётся контейнер API
# (replace_triggered_by), потому что на месте провайдер его не меняет.
#
# start_interval — частота проверок в start_period: без него первая проверка
# шла бы через interval, и wait ждал бы 30 с даже здоровый API. Длительности
# — в формате Go, в котором провайдер читает их обратно ("2s", "30s", "1m0s"):
# "1m" давал бы вечный diff. curl в образе нет, python есть.
locals {
  api_healthcheck = {
    test = [
      "CMD-SHELL",
      "python -c \"import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=2).status == 200 else 1)\"",
    ]
    interval       = "30s"
    timeout        = "5s"
    retries        = 3
    start_period   = "10s"
    start_interval = "1s"
  }
}

resource "terraform_data" "api_healthcheck" {
  input = local.api_healthcheck
}

# Воркер: тот же образ, что у API, другая команда. Портов нет: с остальным
# стеком он говорит только через очередь и базу (`backend-delivery`), наружу —
# исходящими запросами к GitHub API за диффом, правилами и файлами.
resource "docker_container" "worker" {
  name    = "dmc268-worker"
  image   = local.api_image_ref
  restart = "unless-stopped"
  command = ["python", "-m", "app.worker"]

  # По SIGTERM воркер дорабатывает сообщение в работе. Худший случай — два
  # вызова модели (ревью и верификатор) × LLM_TIMEOUT_SECONDS (300) ×
  # LLM_MAX_ATTEMPTS (2) × провайдеры (два с фоллбэком), плюс минута на GitHub
  # и базу. stop_timeout — для docker stop, destroy_grace_seconds — для
  # пересоздания: без него провайдер удаляет контейнер сразу SIGKILL.
  stop_timeout          = local.worker_stop_seconds
  destroy_grace_seconds = local.worker_stop_seconds

  # Те же основания, что у api: образ один.
  read_only     = true
  tmpfs         = { "/tmp" = "rw,noexec,nosuid,size=64m" }
  security_opts = ["no-new-privileges:true"]
  init          = true
  memory        = 512
  memory_swap   = 512
  log_driver    = "json-file"
  log_opts      = local.log_opts

  capabilities {
    drop = ["ALL"]
  }

  # Как у API: воркер пишет в ту же схему, а миграции уже применены шагом
  # деплоя. Готовности брокера depends_on не ждёт; если воркер поднялся
  # раньше, он упадёт на подключении и рестарт поднимет его снова.
  depends_on = [
    docker_container.postgres,
    docker_container.rabbitmq,
  ]

  networks_advanced {
    name = docker_network.backend.name
  }

  networks_advanced {
    name = docker_network.egress.name
  }

  # Секрет вебхука воркеру не нужен, но без него не загрузятся общие
  # настройки; убрать его можно, когда настройки API и воркера разделятся.
  # Реквизиты App нужны: сборка контекста довыбирает дифф, `.review/rules.md`
  # и изменённые файлы через VCS-шлюз.
  # Адаптер модели: без LLM_BACKEND воркер не стартует, и деплой падает на
  # проверке устойчивой работы. Eurorouter подключается только вторым и только
  # флагом llm_hosted_fallback: тогда код проверяемых репозиториев уходит
  # hosted-провайдеру (docs/BACKEND_ARCHITECTURE.md, модель угроз).
  env = concat(local.app_env, [
    "GITHUB_APP_ID=${var.github_app_id}",
    "GITHUB_APP_PRIVATE_KEY=${var.github_app_private_key}",
    "LLM_BACKEND=${var.llm_backend}",
    "OLLAMA_BASE_URL=${var.ollama_base_url}",
    "OLLAMA_MODEL=${var.ollama_model}",
    "LLM_HOSTED_FALLBACK=${var.llm_hosted_fallback}",
    "EUROROUTER_BASE_URL=${var.eurorouter_base_url}",
    "EUROROUTER_MODEL=${var.eurorouter_model}",
    "EUROROUTER_API_KEYS=${var.eurorouter_api_keys}",
  ])
}
