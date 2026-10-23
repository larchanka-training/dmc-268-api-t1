locals {
  # Ротация логов: без неё json-file растёт, пока не кончится диск сервера.
  # Три файла по 10 МБ на контейнер хватает, чтобы разобрать падение после деплоя.
  log_opts = {
    "max-size" = "10m"
    "max-file" = "3"
  }

  # Окружение, без которого не загрузятся общие настройки (`load_settings`):
  # его получают все три контейнера из образа приложения — migrate, api и
  # worker. Одно описание вместо трёх копий, чтобы они не разъезжались.
  app_env = [
    "DATABASE_URL=postgresql+psycopg://${urlencode(var.postgres_user)}:${urlencode(var.postgres_password)}@dmc268-postgres:5432/${var.postgres_db}",
    # %2F — закодированный дефолтный vhost "/": pika разбирает буквальный "//" как пустой vhost.
    "RABBITMQ_URL=amqp://${urlencode(var.rabbitmq_user)}:${urlencode(var.rabbitmq_password)}@dmc268-rabbitmq:5672/%2F",
    "GITHUB_WEBHOOK_SECRET=${var.github_webhook_secret}",
  ]

  # Версия сервиса для телеметрии — тег образа API: deploy.yml публикует образ
  # с тегом по sha коммита, а git внутрь образа не попадает, поэтому версия
  # приходит окружением (OTEL_RESOURCE_ATTRIBUTES).
  service_version = try(regex(":(.+)$", var.api_image)[0], "unknown")

  # Экспорт телеметрии сервисами приложения (change add-observability-stack):
  # коллектор в той же сети. Таймаут OTLP меньше окна graceful shutdown API
  # (5 с), чтобы выгрузка спанов при остановке успевала даже при недоступном
  # коллекторе (замечание из #89).
  telemetry_env = [
    "OTEL_TRACES_EXPORTER=otlp",
    "OTEL_METRICS_EXPORTER=otlp",
    "OTEL_EXPORTER_OTLP_ENDPOINT=http://dmc268-otelcol:4318",
    "OTEL_EXPORTER_OTLP_TIMEOUT=4000",
    "OTEL_RESOURCE_ATTRIBUTES=service.version=${local.service_version},deployment.environment.name=${var.deployment_environment_name}",
  ]

  # Образы стека наблюдаемости — те же теги, что в docker-compose.yml: конфиги
  # в observability/ общие, версии обновляются в обоих местах вместе (у
  # file_log коллектора операторы stanza — beta, обновление с чтением breaking
  # changes).
  otelcol_image           = "otel/opentelemetry-collector-contrib:0.158.0"
  loki_image              = "grafana/loki:3.7.9"
  tempo_image             = "grafana/tempo:3.1.1"
  prometheus_image        = "prom/prometheus:v3.15.1"
  grafana_image           = "grafana/grafana:13.2.4"
  postgres_exporter_image = "prometheuscommunity/postgres-exporter:v0.20.1"

  # Грейс-период остановки воркера, секунды; обоснование — у docker_container.worker.
  # consumer_timeout RabbitMQ его не ограничивает: в 4.4 таймаут подтверждения
  # есть только у quorum-очередей, а очередь review — classic.
  # Деплой ждёт остановку воркера в `tofu apply`: timeout-minutes job deploy в
  # .github/workflows/deploy.yml обязан быть больше максимума (2460 с) с запасом.
  # Та же формула — у stop_grace_period воркера в docker-compose.yml.
  worker_stop_seconds = 2 * 300 * 2 * (var.llm_hosted_fallback ? 2 : 1) + 60
}

resource "docker_network" "dmc268" {
  name = "dmc268"
}

# Имя со сборкой образа: при переходе с postgres:18-alpine (musl) на
# pgvector/pgvector:pg18 (glibc) том заведён заново — библиотеки сортируют
# строки по-разному, и текстовые индексы старого тома были бы неконсистентны.
resource "docker_volume" "postgres" {
  name = "dmc268-postgres-pgvector-data"
}

# Имя с версией: при переходе с 3.13 на 4.3 и с 4.3 на 4.4 том заведён
# заново. Первый раз — потому что прыжок через минорные версии ломает
# классические очереди с данными, второй — потому что 4.4 пишет хранилище
# сообщений classic-очередей в новом формате, и откатить узел на 4.3 уже
# нельзя; данных, которые стоило бы переносить, в брокере не было.
# Следующее обновление, когда в брокере будут данные, — по шагам через каждую
# минорную версию на этом же томе, после `rabbitmqctl enable_feature_flag all`.
resource "docker_volume" "rabbitmq" {
  name = "dmc268-rabbitmq-v44-data"
}

resource "docker_image" "postgres" {
  # Официальный образ postgres со встроенным pgvector: профиль репозитория
  # держит векторы в той же базе, расширение создаёт миграция 0003.
  name         = "pgvector/pgvector:pg18"
  keep_locally = true
}

resource "docker_image" "rabbitmq" {
  name         = "rabbitmq:4.4-alpine"
  keep_locally = true
}

resource "docker_image" "api" {
  # Образ собирается и публикуется в CI, здесь он только забирается по тегу.
  # Раньше он собирался прямо отсюда по хешу исходников; при удалённом
  # docker_host такая сборка ушла бы на сервер — без контекста и без кеша.
  name         = var.api_image
  keep_locally = true
}

resource "docker_container" "postgres" {
  name    = "dmc268-postgres"
  image   = docker_image.postgres.image_id
  restart = "unless-stopped"

  # Только запрет повышения привилегий: entrypoint образа стартует от root,
  # делает chown каталога данных и сам переходит на пользователя postgres.
  # Сброс capabilities или user здесь ломают этот старт.
  security_opts = ["no-new-privileges:true"]
  memory        = 1024
  memory_swap   = 1024
  log_driver    = "json-file"
  log_opts      = local.log_opts

  networks_advanced {
    name = docker_network.dmc268.name
  }

  env = [
    "POSTGRES_USER=${var.postgres_user}",
    "POSTGRES_PASSWORD=${var.postgres_password}",
    "POSTGRES_DB=${var.postgres_db}",
  ]

  ports {
    internal = 5432
    external = var.postgres_port
    ip       = var.internal_bind_ip
  }

  volumes {
    volume_name = docker_volume.postgres.name
    # Не .../data: postgres:18 держит данные в подкаталоге с номером мажорной
    # версии, и монтирование по до-18-й конвенции заставляет entrypoint
    # отказаться стартовать. В docker-compose.yml это уже учтено — здесь
    # расхождение осталось и обнаружилось первым же деплоем.
    container_path = "/var/lib/postgresql"
  }

  healthcheck {
    test     = ["CMD-SHELL", "pg_isready -U ${var.postgres_user} -d ${var.postgres_db}"]
    interval = "10s"
    timeout  = "5s"
    retries  = 5
  }
}

resource "docker_container" "rabbitmq" {
  name    = "dmc268-rabbitmq"
  image   = docker_image.rabbitmq.image_id
  restart = "unless-stopped"

  # Как у postgres: entrypoint сам переходит на пользователя rabbitmq. Порог
  # памяти брокер считает от лимита cgroup, так что memory он учтёт сам.
  security_opts = ["no-new-privileges:true"]
  memory        = 512
  memory_swap   = 512
  log_driver    = "json-file"
  log_opts      = local.log_opts

  networks_advanced {
    name = docker_network.dmc268.name
  }

  env = [
    "RABBITMQ_DEFAULT_USER=${var.rabbitmq_user}",
    "RABBITMQ_DEFAULT_PASS=${var.rabbitmq_password}",
  ]

  ports {
    internal = 5672
    external = var.rabbitmq_port
    ip       = var.internal_bind_ip
  }

  volumes {
    volume_name    = docker_volume.rabbitmq.name
    container_path = "/var/lib/rabbitmq"
  }

  healthcheck {
    test     = ["CMD", "rabbitmq-diagnostics", "-q", "ping"]
    interval = "10s"
    timeout  = "5s"
    retries  = 5
  }
}

resource "docker_container" "migrate" {
  name  = "dmc268-migrate"
  image = docker_image.api.image_id

  # Одноразовый контейнер: отрабатывает и завершается. `attach` заставляет
  # Terraform дождаться конца, а postcondition — упасть на ненулевом коде.
  # Без этого упавшая миграция осталась бы незамеченной, и API поднялся бы
  # против непромигрированной базы (ровно то, что делал прежний main.tf).
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
    name = docker_network.dmc268.name
  }

  env = concat(local.app_env, local.telemetry_env)

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

  networks_advanced {
    name = docker_network.dmc268.name
  }

  # Реквизиты GitHub App: VCS-шлюз у вебхуков (метаданные PR) и у воркера,
  # OAuth и origin — у входа и проверки сессии, ключ токенов пользователя — у
  # входа и ручек репозиториев.
  env = concat(local.app_env, local.telemetry_env, [
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
    name = docker_network.dmc268.name
  }

  # Секрет вебхука воркеру не нужен, но без него не загрузятся общие
  # настройки; убрать его можно, когда настройки API и воркера разделятся.
  # Реквизиты App нужны: сборка контекста довыбирает дифф, `.review/rules.md`
  # и изменённые файлы через VCS-шлюз.
  # Адаптер модели: без LLM_BACKEND воркер не стартует, и деплой падает на
  # проверке устойчивой работы. Eurorouter подключается только вторым и только
  # флагом llm_hosted_fallback: тогда код проверяемых репозиториев уходит
  # hosted-провайдеру (docs/BACKEND_ARCHITECTURE.md, модель угроз).
  env = concat(local.app_env, local.telemetry_env, [
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

# Стек наблюдаемости (change add-observability-stack): коллектор, Loki, Tempo,
# Prometheus, Grafana и postgres-exporter — те же конфиги из observability/,
# что и в локальном compose. Конфиги заливаются в контейнеры при создании
# (upload), поэтому на сервере не нужен чекаут репозитория: tofu читает файлы
# из рабочей копии и передаёт через Docker API. Правка конфига пересоздаёт
# контейнер следующим apply. Алиасы сети повторяют имена сервисов compose —
# одни и те же адреса работают в обоих окружениях (prometheus.yml, grafana
# datasources, endpoint коллектора). Наружу из стека не смотрит ничего:
# Grafana публикуется на internal_bind_ip и открывается SSH-туннелем.

resource "docker_image" "otelcol" {
  name         = local.otelcol_image
  keep_locally = true
}

resource "docker_image" "loki" {
  name         = local.loki_image
  keep_locally = true
}

resource "docker_image" "tempo" {
  name         = local.tempo_image
  keep_locally = true
}

resource "docker_image" "prometheus" {
  name         = local.prometheus_image
  keep_locally = true
}

resource "docker_image" "grafana" {
  name         = local.grafana_image
  keep_locally = true
}

resource "docker_image" "postgres_exporter" {
  name         = local.postgres_exporter_image
  keep_locally = true
}

resource "docker_volume" "collector" {
  name = "dmc268-collector-data"
}

resource "docker_volume" "loki" {
  name = "dmc268-loki-data"
}

resource "docker_volume" "tempo" {
  name = "dmc268-tempo-data"
}

resource "docker_volume" "prometheus" {
  name = "dmc268-prometheus-data"
}

resource "docker_volume" "grafana" {
  name = "dmc268-grafana-data"
}

# Коллектор от root: лог-файлы Docker на сервере читает только root (как
# promtail), и он же создаёт каталог file_storage в томе. Образ по умолчанию —
# пользователь 10001.
resource "docker_container" "otelcol" {
  name    = "dmc268-otelcol"
  image   = docker_image.otelcol.image_id
  restart = "unless-stopped"
  user    = "0"

  memory      = var.observability_memory_mb.collector
  memory_swap = var.observability_memory_mb.collector
  log_driver  = "json-file"
  log_opts    = local.log_opts

  security_opts = ["no-new-privileges:true"]

  # Логи контейнеров (json-file) — источник file_log приёмника, только чтение.
  volumes {
    host_path      = "/var/lib/docker/containers"
    container_path = "/var/lib/docker/containers"
    read_only      = true
  }

  volumes {
    volume_name    = docker_volume.collector.name
    container_path = "/var/lib/otelcol"
  }

  upload {
    content = file("${path.module}/observability/otelcol.yaml")
    file    = "/etc/otelcol-contrib/config.yaml"
  }

  env = [
    # Все *-json.log контейнеров; не-JSON строки (логи самих контейнеров
    # стека) коллектор отбрасывает — см. on_error: drop в конфиге.
    "OTELCOL_LOG_FILES=/var/lib/docker/containers/*/*-json.log",
  ]

  networks_advanced {
    name    = docker_network.dmc268.name
    aliases = ["collector"]
  }

  depends_on = [
    docker_container.loki,
    docker_container.tempo,
  ]
}

resource "docker_container" "loki" {
  name    = "dmc268-loki"
  image   = docker_image.loki.image_id
  restart = "unless-stopped"

  memory      = var.observability_memory_mb.loki
  memory_swap = var.observability_memory_mb.loki
  log_driver  = "json-file"
  log_opts    = local.log_opts

  security_opts = ["no-new-privileges:true"]

  upload {
    content = file("${path.module}/observability/loki.yaml")
    file    = "/etc/loki/config.yaml"
  }

  command = ["-config.file=/etc/loki/config.yaml"]

  volumes {
    volume_name    = docker_volume.loki.name
    container_path = "/loki"
  }

  networks_advanced {
    name    = docker_network.dmc268.name
    aliases = ["loki"]
  }
}

resource "docker_container" "tempo" {
  name    = "dmc268-tempo"
  image   = docker_image.tempo.image_id
  restart = "unless-stopped"

  memory      = var.observability_memory_mb.tempo
  memory_swap = var.observability_memory_mb.tempo
  log_driver  = "json-file"
  log_opts    = local.log_opts

  security_opts = ["no-new-privileges:true"]

  upload {
    content = file("${path.module}/observability/tempo.yaml")
    file    = "/etc/tempo/config.yaml"
  }

  command = ["-config.file=/etc/tempo/config.yaml"]

  volumes {
    volume_name    = docker_volume.tempo.name
    container_path = "/var/tempo"
  }

  networks_advanced {
    name    = docker_network.dmc268.name
    aliases = ["tempo"]
  }
}

resource "docker_container" "prometheus" {
  name    = "dmc268-prometheus"
  image   = docker_image.prometheus.image_id
  restart = "unless-stopped"

  memory      = var.observability_memory_mb.prometheus
  memory_swap = var.observability_memory_mb.prometheus
  log_driver  = "json-file"
  log_opts    = local.log_opts

  security_opts = ["no-new-privileges:true"]

  upload {
    content = file("${path.module}/observability/prometheus.yml")
    file    = "/etc/prometheus/prometheus.yml"
  }

  command = [
    "--config.file=/etc/prometheus/prometheus.yml",
    # Хранение метрик — 30 дней (как retention логов Loki).
    "--storage.tsdb.retention.time=30d",
  ]

  volumes {
    volume_name    = docker_volume.prometheus.name
    container_path = "/prometheus"
  }

  networks_advanced {
    name    = docker_network.dmc268.name
    aliases = ["prometheus"]
  }
}

# Доступ к Grafana — по учётке: анонимный вход выключен, пароль админа из
# переменной (локальный compose, наоборот, поднимает Grafana без логина).
resource "docker_container" "grafana" {
  name    = "dmc268-grafana"
  image   = docker_image.grafana.image_id
  restart = "unless-stopped"

  memory      = var.observability_memory_mb.grafana
  memory_swap = var.observability_memory_mb.grafana
  log_driver  = "json-file"
  log_opts    = local.log_opts

  security_opts = ["no-new-privileges:true"]

  upload {
    content = file("${path.module}/observability/grafana/provisioning/datasources/datasources.yaml")
    file    = "/etc/grafana/provisioning/datasources/datasources.yaml"
  }

  env = [
    "GF_SECURITY_ADMIN_PASSWORD=${var.grafana_admin_password}",
    "GF_AUTH_ANONYMOUS_ENABLED=false",
    "GF_USERS_ALLOW_SIGN_UP=false",
  ]

  volumes {
    volume_name    = docker_volume.grafana.name
    container_path = "/var/lib/grafana"
  }

  ports {
    internal = 3000
    external = var.grafana_port
    ip       = var.internal_bind_ip
  }

  networks_advanced {
    name    = docker_network.dmc268.name
    aliases = ["grafana"]
  }

  depends_on = [
    docker_container.loki,
    docker_container.tempo,
    docker_container.prometheus,
  ]
}

# Экспортёр метрик PostgreSQL (design, решение 3): кода не касается, скреится
# Prometheus-ом (job postgres).
resource "docker_container" "postgres_exporter" {
  name    = "dmc268-postgres-exporter"
  image   = docker_image.postgres_exporter.image_id
  restart = "unless-stopped"

  memory      = var.observability_memory_mb.postgres_exporter
  memory_swap = var.observability_memory_mb.postgres_exporter
  log_driver  = "json-file"
  log_opts    = local.log_opts

  security_opts = ["no-new-privileges:true"]

  env = [
    "DATA_SOURCE_URI=dmc268-postgres:5432/${var.postgres_db}?sslmode=disable",
    "DATA_SOURCE_USER=${var.postgres_user}",
    "DATA_SOURCE_PASS=${var.postgres_password}",
  ]

  networks_advanced {
    name    = docker_network.dmc268.name
    aliases = ["postgres-exporter"]
  }

  depends_on = [docker_container.postgres]
}
