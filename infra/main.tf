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
  # приходит окружением (OTEL_RESOURCE_ATTRIBUTES). Тег — после последнего
  # двоеточия и без слэшей: реестр с портом (localhost:5000/repo:sha) не
  # ломают разбор.
  service_version = try(regex(":(?P<tag>[^/:]+)$", var.api_image)["tag"], "unknown")

  # Экспорт телеметрии сервисами приложения (change add-observability-stack):
  # коллектор в той же сети, логи и трейсы с метриками идут по OTLP. Таймаут
  # OTLP меньше окна graceful shutdown API (5 с), чтобы выгрузка спанов при
  # остановке успевала даже при недоступном коллекторе (замечание из #89).
  # Python SDK читает таймаут в СЕКУНДАХ, вопреки спеке OTel (мс).
  # Стек выключен — список пуст: SDK без OTEL_*_EXPORTER ничего не отправляет
  # и в лог ошибок не пишет, а concat в env api/воркера/миграций не ломается.
  telemetry_env = var.observability_enabled ? [
    "OTEL_TRACES_EXPORTER=otlp",
    "OTEL_METRICS_EXPORTER=otlp",
    "OTEL_LOGS_EXPORTER=otlp",
    "OTEL_EXPORTER_OTLP_ENDPOINT=http://dmc268-otelcol:4318",
    "OTEL_EXPORTER_OTLP_TIMEOUT=4",
  ] : []

  # Общая часть OTEL_RESOURCE_ATTRIBUTES; service.instance.id каждый контейнер
  # дописывает своим стабильным именем (см. env ниже): случайный UUID на
  # процесс растил бы в Prometheus серии exported_instance — ресурс
  # конвертируется в лейблы рядов.
  telemetry_resource = "service.version=${local.service_version},deployment.environment.name=${var.deployment_environment_name}"

  # Образы стека наблюдаемости — те же версии, что в docker-compose.yml:
  # конфиги в observability/ общие. Зафиксированы дайджестом манифест-листа
  # (multi-arch, разрешён `docker buildx imagetools inspect <имя:тег>`) —
  # одинаково в обоих местах; тег в комментарии рядом, обновление образа —
  # осознанная замена дайджеста в обоих файлах с чтением breaking changes.
  otelcol_image       = "otel/opentelemetry-collector-contrib@sha256:c5918f78992ee73b0d6f0e599423ac5ec52dd5d9726733114d6eca53d5a32ed5"
  loki_image          = "grafana/loki@sha256:0db216b630ae1f348a1584c67f3cead8b4c1fc839169a39501db664744158e28"
  tempo_image         = "grafana/tempo@sha256:64e2fbd56d9c2b3412a4b430a58ea59e91a670b1e75f920a0c702213ad145044"
  prometheus_image    = "prom/prometheus@sha256:fa3302279710be2f21a8301ec17a8b4f2301fd829bd113839b3b6b02b6f191a2"
  grafana_image       = "grafana/grafana@sha256:dbbc38ea21103cefb50396340cd88afec779c7e070b0a7bf5a75b4ce46651d3f"
  node_exporter_image = "quay.io/prometheus/node-exporter@sha256:d00a542e409ee618a4edc67da14dd48c5da66726bbd5537ab2af9c1dfc442c8a"
  cadvisor_image      = "gcr.io/cadvisor/cadvisor@sha256:f40e65878e25c2e78ea037f73a449527a0fb994e303dc3e34cb6b187b4b91435"
  # socket-proxy: доступ cAdvisor к Docker API без docker.sock (контейнеры и
  # информация о сервере; минимум прав — см. env у контейнера ниже).
  docker_socket_proxy_image = "tecnativa/docker-socket-proxy@sha256:3400c429c5f9e1b21d62130fb93b16e2e772d4fb7695bd52fc2b743800b9fe9e"

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

  # Библиотека pg_stat_statements нужна ручной диагностике медленных запросов
  # (расширение создаёт миграция 0013); загружаем её только вместе со стеком
  # наблюдаемости, при observability_enabled = false command = nil возвращает
  # образу его собственный CMD. receiver postgresql коллектора в расширении не
  # нуждается — метрики базы он снимает обычными вьюхами статистики. Смена
  # command пересоздаёт контейнер базы (том сохраняется) — она происходит
  # только в паре с переключателем стека. Локальный compose preload держит
  # всегда (накладные расходы ничтожны, переключателя в compose нет).
  command = var.observability_enabled ? ["postgres", "-c", "shared_preload_libraries=pg_stat_statements"] : null

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

  env = concat(local.app_env, local.telemetry_env, [
    "OTEL_RESOURCE_ATTRIBUTES=${local.telemetry_resource},service.instance.id=dmc268-migrate",
    # Пароль роли мониторинга dmc268_monitor: миграция 0013 создаёт роль с
    # ним, коллектор подключается под ней (receiver postgresql). Один источник
    # с env коллектора (POSTGRESQL_PASSWORD ниже) — иначе роль и подключение
    # разъедутся.
    "POSTGRES_MONITOR_PASSWORD=${var.postgres_monitor_password}",
  ])

  # depends_on у docker-провайдера задаёт только порядок создания, но не ждёт
  # готовности. Прежняя версия ждала открытия TCP-порта — этого мало:
  # PostgreSQL открывает порт раньше, чем начинает принимать запросы, и окно
  # между этим давало бы редкие падения деплоя без причины. Повторяем саму
  # миграцию: успех означает и готовность базы, и применённую схему. Настоящая
  # ошибка в миграции тоже переживёт все попытки и уйдёт ненулевым кодом.
  # После миграций — перегенерация пароля роли мониторинга dmc268_monitor
  # (создаёт миграция 0013): ALTER ROLE идемпотентен, пароль сводится с env
  # коллектора (POSTGRESQL_PASSWORD) на каждом деплое, а старый пароль из
  # прошлого секрета перестаёт действовать. Пароль — параметром запроса
  # psycopg, не литералом команды: литерал осел бы в /proc и docker logs.
  # track_utility выключается той же транзакцией (SET LOCAL живёт до COMMIT):
  # иначе ALTER ROLE ... PASSWORD лёг бы в pg_stat_statements открытым текстом.
  # Тот же шаг перегенерации — у сервиса api в docker-compose.yml.
  command = [
    "sh", "-c",
    <<-EOT
    ok=0
    for i in $(seq 1 30); do
      if alembic upgrade head; then ok=1; break; fi
      sleep 2
    done
    [ "$ok" = 1 ] || exit 1
    python - <<'PY' || exit 1
    import os

    import psycopg

    # DATABASE_URL настроек — под схемой SQLAlchemy (postgresql+psycopg://);
    # psycopg ждёт стандартную схему postgresql://. Привязка значений — на
    # клиенте (ClientCursor): серверная не проходит по грамматике ALTER ROLE
    # (PASSWORD принимает только литерал, не $1), а литерал руками собирать
    # нельзя — psycopg экранирует сам.
    dsn = os.environ["DATABASE_URL"].replace("postgresql+psycopg://", "postgresql://", 1)
    with psycopg.connect(dsn, cursor_factory=psycopg.ClientCursor) as conn:
        with conn.transaction():
            conn.execute("SET LOCAL pg_stat_statements.track_utility = off")
            # Имя роли зашито — то же, что в миграции 0013 и env коллектора.
            conn.execute(
                "ALTER ROLE dmc268_monitor PASSWORD %s",
                (os.environ["POSTGRES_MONITOR_PASSWORD"],),
            )
    PY
    EOT
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

  # Остановка api: SIGTERM и ожидание. Худший случай — 5 с graceful shutdown
  # (рвутся потоки SSE) + 4 с выгрузки спанов (OTEL_EXPORTER_OTLP_TIMEOUT,
  # local.telemetry_env) с запасом: без него дефолтные 10 с Docker — впритык,
  # и lifespan оборвался бы до выгрузки спанов (замечание из #89 про обе
  # половины). destroy_grace_seconds — то же окно при пересоздании провайдером;
  # 15 с — как у stop_grace_period api в docker-compose.yml.
  stop_timeout          = 15
  destroy_grace_seconds = 15

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
    "OTEL_RESOURCE_ATTRIBUTES=${local.telemetry_resource},service.instance.id=dmc268-api",
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
    "OTEL_RESOURCE_ATTRIBUTES=${local.telemetry_resource},service.instance.id=dmc268-worker",
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
# Prometheus, Grafana, node-exporter, cAdvisor и socket-proxy — те же конфиги
# из observability/, что и в локальном compose. Метрики базы снимает receiver
# postgresql самого коллектора (решение #89) — отдельного экспортёра нет.
# Конфиги заливаются в контейнеры при создании
# (upload), поэтому на сервере не нужен чекаут репозитория: tofu читает файлы
# из рабочей копии и передаёт через Docker API. Правка конфига пересоздаёт
# контейнер следующим apply. Алиасы сети повторяют имена сервисов compose —
# одни и те же адреса работают в обоих окружениях (prometheus.yml, grafana
# datasources, endpoint коллектора). Наружу из стека не смотрит ничего:
# Grafana публикуется на internal_bind_ip и открывается SSH-туннелем.
# Стек поднимается только при observability_enabled: count = 1 : 0 у каждого
# его ресурса — образа, тома, сети и контейнера; выключение списка телеметрии
# см. local.telemetry_env.

resource "docker_image" "otelcol" {
  count        = var.observability_enabled ? 1 : 0
  name         = local.otelcol_image
  keep_locally = true
}

resource "docker_image" "loki" {
  count        = var.observability_enabled ? 1 : 0
  name         = local.loki_image
  keep_locally = true
}

resource "docker_image" "tempo" {
  count        = var.observability_enabled ? 1 : 0
  name         = local.tempo_image
  keep_locally = true
}

resource "docker_image" "prometheus" {
  count        = var.observability_enabled ? 1 : 0
  name         = local.prometheus_image
  keep_locally = true
}

resource "docker_image" "grafana" {
  count        = var.observability_enabled ? 1 : 0
  name         = local.grafana_image
  keep_locally = true
}

resource "docker_image" "node_exporter" {
  count        = var.observability_enabled ? 1 : 0
  name         = local.node_exporter_image
  keep_locally = true
}

resource "docker_image" "cadvisor" {
  count        = var.observability_enabled ? 1 : 0
  name         = local.cadvisor_image
  keep_locally = true
}

resource "docker_image" "docker_socket_proxy" {
  count        = var.observability_enabled ? 1 : 0
  name         = local.docker_socket_proxy_image
  keep_locally = true
}

resource "docker_volume" "loki" {
  count = var.observability_enabled ? 1 : 0
  name  = "dmc268-loki-data"
}

resource "docker_volume" "tempo" {
  count = var.observability_enabled ? 1 : 0
  name  = "dmc268-tempo-data"
}

resource "docker_volume" "prometheus" {
  count = var.observability_enabled ? 1 : 0
  name  = "dmc268-prometheus-data"
}

resource "docker_volume" "grafana" {
  count = var.observability_enabled ? 1 : 0
  name  = "dmc268-grafana-data"
}

# Отдельная сеть для Docker API через socket-proxy: в ней только proxy и
# cAdvisor — остальным контейнерам стека и приложения доступ к демону Docker
# не нужен. cAdvisor — единственный, кто ходит и в эту сеть (к proxy), и в
# dmc268 (оттуда его скреит Prometheus).
resource "docker_network" "observability_docker_proxy" {
  count = var.observability_enabled ? 1 : 0
  name  = "dmc268-docker-proxy"
}

resource "docker_container" "otelcol" {
  count   = var.observability_enabled ? 1 : 0
  name    = "dmc268-otelcol"
  image   = docker_image.otelcol[0].image_id
  restart = "unless-stopped"

  memory      = var.observability_memory_mb.collector
  memory_swap = var.observability_memory_mb.collector
  log_driver  = "json-file"
  log_opts    = local.log_opts

  # Капли и запрет новых привилегий — как у api; read_only здесь НЕ ставим:
  # провайдер заливает конфиг upload-ом (CopyToContainer, как docker cp),
  # а демон отказывает в записи в read-only rootfs ещё до пути — apply
  # упал бы на создании контейнера. Локальный коллектор read_only: конфиг
  # приходит read-only маунтом (docker-compose.yml).
  tmpfs         = { "/tmp" = "rw,noexec,nosuid,size=64m" }
  security_opts = ["no-new-privileges:true"]

  capabilities {
    drop = ["ALL"]
  }

  upload {
    content = file("${path.module}/observability/otelcol.yaml")
    file    = "/etc/otelcol-contrib/config.yaml"
  }

  env = [
    # Реквизиты роли мониторинга базы (receiver postgresql в otelcol.yaml):
    # роль создаёт миграция 0013 с тем же паролем (docker_container.migrate),
    # имя совпадает с переменной POSTGRES_MONITOR_PASSWORD настроек.
    "POSTGRESQL_USERNAME=dmc268_monitor",
    "POSTGRESQL_PASSWORD=${var.postgres_monitor_password}",
    "POSTGRESQL_ENDPOINT=dmc268-postgres:5432",
    "POSTGRESQL_DATABASE=${var.postgres_db}",
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
  count   = var.observability_enabled ? 1 : 0
  name    = "dmc268-loki"
  image   = docker_image.loki[0].image_id
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
    volume_name    = docker_volume.loki[0].name
    container_path = "/loki"
  }

  networks_advanced {
    name    = docker_network.dmc268.name
    aliases = ["loki"]
  }
}

resource "docker_container" "tempo" {
  count   = var.observability_enabled ? 1 : 0
  name    = "dmc268-tempo"
  image   = docker_image.tempo[0].image_id
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
    volume_name    = docker_volume.tempo[0].name
    container_path = "/var/tempo"
  }

  networks_advanced {
    name    = docker_network.dmc268.name
    aliases = ["tempo"]
  }
}

resource "docker_container" "prometheus" {
  count   = var.observability_enabled ? 1 : 0
  name    = "dmc268-prometheus"
  image   = docker_image.prometheus[0].image_id
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
    # Потолок по размеру, а не только по времени: всплеск кардинальности
    # не должен забить диск раньше срока хранения. Свободное место видно
    # алертам (#100) по node_filesystem_avail_bytes от node-exporter.
    "--storage.tsdb.retention.size=8GB",
  ]

  volumes {
    volume_name    = docker_volume.prometheus[0].name
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
  count   = var.observability_enabled ? 1 : 0
  name    = "dmc268-grafana"
  image   = docker_image.grafana[0].image_id
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

  # Провижининг дашборда: файл-провайдер и сам дашборд, как у datasources.
  # Каталог сканируется Grafana на *.json, yaml провайдера там же игнорируется.
  upload {
    content = file("${path.module}/observability/grafana/provisioning/dashboards/dashboards.yaml")
    file    = "/etc/grafana/provisioning/dashboards/dashboards.yaml"
  }

  upload {
    content = file("${path.module}/observability/grafana/provisioning/dashboards/dmc268-overview.json")
    file    = "/etc/grafana/provisioning/dashboards/dmc268-overview.json"
  }

  env = [
    "GF_SECURITY_ADMIN_PASSWORD=${var.grafana_admin_password}",
    "GF_AUTH_ANONYMOUS_ENABLED=false",
    "GF_USERS_ALLOW_SIGN_UP=false",
    # Телеметрия вендора off: статистика использования и проверка обновлений
    # не должны уходить с сервера наружу. То же в compose и у Loki/Tempo в их
    # конфигах (loki.yaml, tempo.yaml).
    "GF_ANALYTICS_REPORTING_ENABLED=false",
    "GF_ANALYTICS_CHECK_FOR_UPDATES=false",
  ]

  volumes {
    volume_name    = docker_volume.grafana[0].name
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

# Метрики сервера (алерты #100): CPU, память, диск, файловые системы хоста.
# Каноничный запуск образа: корень хоста только для чтения (--path.rootfs),
# /proc и /sys — источники статистики ядра. Имя контейнера с префиксом
# dmc268-, алиас сети — как имя сервиса compose.
resource "docker_container" "node_exporter" {
  count   = var.observability_enabled ? 1 : 0
  name    = "dmc268-node-exporter"
  image   = docker_image.node_exporter[0].image_id
  restart = "unless-stopped"

  memory      = var.observability_memory_mb.node_exporter
  memory_swap = var.observability_memory_mb.node_exporter
  log_driver  = "json-file"
  log_opts    = local.log_opts

  security_opts = ["no-new-privileges:true"]

  command = [
    "--path.rootfs=/rootfs",
    "--path.procfs=/host/proc",
    "--path.sysfs=/host/sys",
  ]

  volumes {
    host_path      = "/"
    container_path = "/rootfs"
    read_only      = true
  }

  volumes {
    host_path      = "/proc"
    container_path = "/host/proc"
    read_only      = true
  }

  volumes {
    host_path      = "/sys"
    container_path = "/host/sys"
    read_only      = true
  }

  networks_advanced {
    name    = docker_network.dmc268.name
    aliases = ["node-exporter"]
  }
}

# Метрики контейнеров: CPU и память каждого, включая сам стек наблюдаемости.
# К Docker API cAdvisor ходит только через socket-proxy (флаг -docker —
# проверено по --help образа v0.52.1; ENV у образа флаги не читает): маунтов
# docker.sock и /var/lib/docker больше нет. Маунты / и /sys — каноничные из
# документации cAdvisor (только чтение): без них не видно cgroup-иерархии, по
# которой находятся контейнеры, — проверено запуском. Без --privileged и
# /dev/kmsg недоступны OOM- и perf-коллекторы, CPU, память, диски и сеть
# остаются. Новые контейнеры cAdvisor находит сам, без подписки на события
# Docker: опрашивает cgroup-иерархию и запрашивает у proxy inspect новых
# (проверено запуском нового контейнера рядом).
resource "docker_container" "cadvisor" {
  count   = var.observability_enabled ? 1 : 0
  name    = "dmc268-cadvisor"
  image   = docker_image.cadvisor[0].image_id
  restart = "unless-stopped"

  memory      = var.observability_memory_mb.cadvisor
  memory_swap = var.observability_memory_mb.cadvisor
  log_driver  = "json-file"
  log_opts    = local.log_opts

  security_opts = ["no-new-privileges:true"]

  command = [
    # Имя dockerproxy резолвится в отдельной сети socket-proxy (алиас у
    # docker_container.docker_socket_proxy) — так же контейнер зовут и в
    # docker-compose.yml.
    "--docker=tcp://dockerproxy:2375",
  ]

  volumes {
    host_path      = "/"
    container_path = "/rootfs"
    read_only      = true
  }

  volumes {
    host_path      = "/sys"
    container_path = "/sys"
    read_only      = true
  }

  networks_advanced {
    name    = docker_network.dmc268.name
    aliases = ["cadvisor"]
  }

  networks_advanced {
    name = docker_network.observability_docker_proxy[0].name
  }

  depends_on = [docker_container.docker_socket_proxy]
}

# Прокси над Docker API (/var/run/docker.sock) с доступом по белому списку
# эндпоинтов: cAdvisor — единственный клиент, docker.sock контейнерам больше
# не монтируется. Права — минимальный работающий набор (проверено запуском
# cAdvisor через proxy): CONTAINERS=1 — список, inspect и статистика
# контейнеров; INFO=1 — /info и /version, без них cAdvisor падает на проверке
# Docker-фабрики («403 Forbidden» на /v1.45/info) и не отдаёт метрики
# контейнеров вовсе. События Docker (/events) не нужны: новые контейнеры
# находятся опросом cgroup-иерархии. Сеть — отдельная, только proxy и cAdvisor.
resource "docker_container" "docker_socket_proxy" {
  count   = var.observability_enabled ? 1 : 0
  name    = "dmc268-docker-proxy"
  image   = docker_image.docker_socket_proxy[0].image_id
  restart = "unless-stopped"

  memory      = var.observability_memory_mb.docker_proxy
  memory_swap = var.observability_memory_mb.docker_proxy
  log_driver  = "json-file"
  log_opts    = local.log_opts

  # Как у postgres: entrypoint образа сам управляет привилегиями haproxy.
  security_opts = ["no-new-privileges:true"]

  env = [
    "CONTAINERS=1",
    "INFO=1",
  ]

  volumes {
    host_path      = "/var/run/docker.sock"
    container_path = "/var/run/docker.sock"
    read_only      = true
  }

  networks_advanced {
    name    = docker_network.observability_docker_proxy[0].name
    aliases = ["dockerproxy"]
  }
}
