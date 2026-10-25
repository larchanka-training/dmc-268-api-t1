# Приложение: образ API из реестра и три контейнера из него — migrate, api и
# worker.

resource "docker_image" "api" {
  # Образ собирается и публикуется в CI, здесь он только забирается — по
  # digest, который вернула сборка (`api_image` без него не пройдёт проверку).
  # Раньше он собирался прямо отсюда по хешу исходников; при удалённом
  # docker_host такая сборка ушла бы на сервер — без контекста и без кеша.
  name         = var.api_image
  keep_locally = true
}

resource "docker_container" "migrate" {
  name  = "dmc268-migrate"
  image = docker_image.api.image_id

  # Одноразовый контейнер: отрабатывает и завершается. `attach` заставляет
  # OpenTofu дождаться конца, а postcondition — упасть на ненулевом коде.
  # Без этого упавшая миграция осталась бы незамеченной, и API поднялся бы
  # против непромигрированной базы.
  must_run = false
  attach   = true
  logs     = true

  # Наш образ: пользователь не root (USER в Dockerfile), приложение ничего не
  # пишет на диск, поэтому файловая система только для чтения и без capabilities.
  read_only     = true
  tmpfs         = { "/tmp" = "rw,noexec,nosuid,size=64m" }
  security_opts = ["no-new-privileges:true"]
  init          = true
  memory        = 256
  memory_swap   = 256
  log_driver    = "json-file"
  log_opts      = local.log_opts

  capabilities {
    drop = ["ALL"]
  }

  depends_on = [docker_container.postgres]

  networks_advanced {
    name = docker_network.backend.name
  }

  env = local.app_env

  # depends_on у docker-провайдера задаёт только порядок создания, но не ждёт
  # готовности. Прежняя версия ждала открытия TCP-порта — этого мало:
  # PostgreSQL открывает порт раньше, чем начинает принимать запросы, и окно
  # между этим давало бы редкие падения деплоя без причины. Повторяем саму
  # миграцию: успех означает и готовность базы, и применённую схему. Настоящая
  # ошибка в миграции тоже переживёт все попытки и уйдёт ненулевым кодом.
  command = [
    "sh", "-c",
    "for i in $(seq 1 30); do alembic upgrade head && exit 0; sleep 2; done; exit 1",
  ]

  lifecycle {
    postcondition {
      condition     = self.exit_code == 0
      error_message = "Миграции завершились с ненулевым кодом — деплой остановлен до старта API."
    }
  }
}

resource "docker_container" "api" {
  name    = "dmc268-api"
  image   = docker_image.api.image_id
  restart = "unless-stopped"

  # Без destroy_grace_seconds провайдер при пересоздании удаляет контейнер
  # принудительно (SIGKILL): lifespan не завершается, спаны не выгружаются.
  # С ним — сначала SIGTERM и ожидание остановки; 10 с — как у `docker stop`.
  # Укладываемся благодаря `--timeout-graceful-shutdown 5` в CMD образа: открытые
  # потоки SSE uvicorn рвёт через 5 с, остальное время — на lifespan.
  destroy_grace_seconds = 10

  # Те же основания, что у migrate: образ один.
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

  # Миграции — тоже предусловие старта, не только живые postgres и rabbitmq.
  depends_on = [
    docker_container.postgres,
    docker_container.rabbitmq,
    docker_container.migrate,
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

  # /health существует именно для этого. Без healthcheck Docker знает только,
  # что процесс не завершился, и зависший uvicorn остался бы живым в его
  # представлении до следующего деплоя. curl в образе нет, python есть.
  healthcheck {
    test = [
      "CMD-SHELL",
      "python -c \"import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=2).status == 200 else 1)\"",
    ]
    interval     = "30s"
    timeout      = "5s"
    retries      = 3
    start_period = "10s"
  }
}

# Воркер: тот же образ, что у API, другая команда. Портов нет: с остальным
# стеком он говорит только через очередь и базу (`backend-delivery`), наружу —
# исходящими запросами к GitHub API за диффом, правилами и файлами.
resource "docker_container" "worker" {
  name    = "dmc268-worker"
  image   = docker_image.api.image_id
  restart = "unless-stopped"
  command = ["python", "-m", "app.worker"]

  # По SIGTERM воркер дорабатывает сообщение в работе. Худший случай — два
  # вызова модели (ревью и верификатор) × LLM_TIMEOUT_SECONDS (300) ×
  # LLM_MAX_ATTEMPTS (2) × провайдеры (два с фоллбэком), плюс минута на GitHub
  # и базу. stop_timeout — для docker stop, destroy_grace_seconds — для
  # пересоздания: без него провайдер удаляет контейнер сразу SIGKILL.
  stop_timeout          = local.worker_stop_seconds
  destroy_grace_seconds = local.worker_stop_seconds

  # Те же основания, что у migrate: образ один.
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

  # Как у API: воркер пишет в ту же схему, поэтому стартует только после
  # миграций. Готовности брокера depends_on не ждёт; если воркер поднялся
  # раньше, он упадёт на подключении и рестарт поднимет его снова.
  depends_on = [
    docker_container.postgres,
    docker_container.rabbitmq,
    docker_container.migrate,
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
