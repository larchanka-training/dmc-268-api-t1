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

  # Грейс-период остановки воркера, секунды; обоснование — у docker_container.worker.
  # consumer_timeout RabbitMQ его не ограничивает: в 4.3 таймаут подтверждения
  # есть только у quorum-очередей, а очередь review — classic.
  # Деплой ждёт остановку воркера в `tofu apply`: timeout-minutes job deploy в
  # .github/workflows/deploy.yml обязан быть больше максимума (2460 с) с запасом.
  # Та же формула — у stop_grace_period воркера в docker-compose.yml.
  worker_stop_seconds = 2 * 300 * 2 * (var.llm_hosted_fallback ? 2 : 1) + 60
}

# Три сети вместо одной общей — каждый контейнер видит только тех, с кем говорит.
#
# dmc268 — сеть публичного входа: интерфейс и API. Имя прежнее: контейнер
# интерфейса подключается к ней по имени (data "docker_network" в dmc-268-ui-t1),
# и переименование развело бы стеки.
resource "docker_network" "dmc268" {
  name = "dmc268"
}

# Сеть данных: база, реплика, брокер и те, кто с ними работает. internal — у
# сети нет шлюза: из неё не выйти в интернет, а интерфейс в неё не подключён,
# поэтому база и брокер недоступны ни снаружи, ни с публичного входа.
# Публиковать порт с такой сети Docker не умеет, поэтому у базы и брокера
# портов на хосте больше нет: отладка — через `docker exec`.
resource "docker_network" "backend" {
  name     = "dmc268-backend"
  internal = true
}

# Исходящая сеть воркера: GitHub API и провайдер модели. Отдельно от сети
# входа, чтобы воркер не был виден интерфейсу; портов воркер не публикует.
resource "docker_network" "egress" {
  name = "dmc268-egress"
}

# Имя со сборкой образа: при переходе с postgres:18-alpine (musl) на
# pgvector/pgvector:pg18 (glibc) том заведён заново — библиотеки сортируют
# строки по-разному, и текстовые индексы старого тома были бы неконсистентны.
resource "docker_volume" "postgres" {
  name = "dmc268-postgres-pgvector-data"
}

# Имя с версией: при переходе с 3.13 на 4.3 том заведён заново, потому что
# прыжок через минорные версии ломает классические очереди с данными.
# Следующее обновление, когда в брокере будут данные, — по шагам через каждую
# минорную версию на этом же томе.
resource "docker_volume" "rabbitmq" {
  name = "dmc268-rabbitmq-v4-data"
}

# Свой том у реплики: это полная физическая копия основного каталога данных.
# Удалённый том реплика пересоберёт сама (pg_basebackup при пустом каталоге).
resource "docker_volume" "postgres_replica" {
  name = "dmc268-postgres-replica-data"
}

# Резервные копии лежат на том же диске сервера: от сбоя диска или сервера
# они не спасают, только от ошибочного удаления данных и испорченной базы.
# Вынос копий за пределы сервера — отдельная задача.
resource "docker_volume" "postgres_backups" {
  name = "dmc268-postgres-backups"
}

resource "docker_image" "postgres" {
  # Официальный образ postgres со встроенным pgvector: профиль репозитория
  # держит векторы в той же базе, расширение создаёт миграция 0003.
  name         = "pgvector/pgvector:pg18"
  keep_locally = true
}

resource "docker_image" "rabbitmq" {
  name         = "rabbitmq:4.3-alpine"
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
    name = docker_network.backend.name
  }

  env = [
    "POSTGRES_USER=${var.postgres_user}",
    "POSTGRES_PASSWORD=${var.postgres_password}",
    "POSTGRES_DB=${var.postgres_db}",
  ]

  # wal_level=replica и max_wal_senders у PostgreSQL 18 уже такие, какие нужны
  # реплике; задаём только то, чего нет по умолчанию. max_slot_wal_keep_size —
  # потолок WAL, который удержит слот реплики: без него упавшая реплика копила
  # бы WAL на основном сервере, пока не кончится диск. Перешагнувшая потолок
  # реплика теряет слот; при следующем старте она заметит это сама и
  # пересоберётся (infra/postgres/replica-entrypoint.sh).
  command = [
    "postgres",
    "-c", "hba_file=/etc/postgresql/pg_hba.conf",
    "-c", "max_slot_wal_keep_size=${var.postgres_max_slot_wal_keep_size}",
  ]

  upload {
    file    = "/etc/postgresql/pg_hba.conf"
    content = file("${path.module}/postgres/pg_hba.conf")
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
    name = docker_network.backend.name
  }

  env = [
    "RABBITMQ_DEFAULT_USER=${var.rabbitmq_user}",
    "RABBITMQ_DEFAULT_PASS=${var.rabbitmq_password}",
  ]

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

# Роль replicator для реплики. Одноразовый контейнер по образцу migrate:
# init-скрипты образа на уже заведённом томе не выполняются, поэтому роль
# заводится здесь, идемпотентно. Запускается не на каждом деплое, а когда
# сменились пароль или SQL либо пересоздана основная база — её контейнер или
# том (см. replace_triggered_by). Слот заводит сама реплика.
resource "docker_container" "replication_setup" {
  name  = "dmc268-replication-setup"
  image = docker_image.postgres.image_id

  must_run = false
  attach   = true
  logs     = true

  read_only     = true
  tmpfs         = { "/tmp" = "rw,noexec,nosuid,size=16m" }
  security_opts = ["no-new-privileges:true"]
  user          = "postgres"
  memory        = 64
  memory_swap   = 64
  log_driver    = "json-file"
  log_opts      = local.log_opts

  capabilities {
    drop = ["ALL"]
  }

  depends_on = [docker_container.postgres]

  networks_advanced {
    name = docker_network.backend.name
  }

  # SQL приходит переменной окружения, а не файлом: upload в контейнер с
  # файловой системой только для чтения Docker не пускает.
  env = [
    "PGHOST=dmc268-postgres",
    "PGUSER=${var.postgres_user}",
    "PGPASSWORD=${var.postgres_password}",
    "PGDATABASE=${var.postgres_db}",
    "REPLICATION_PASSWORD=${var.postgres_replication_password}",
    "SETUP_SQL=${file("${path.module}/postgres/replication-setup.sql")}",
  ]

  # Как у migrate: depends_on не ждёт готовности базы, поэтому повторяем.
  command = [
    "sh", "-c",
    "for i in $(seq 1 30); do printf '%s\\n' \"$SETUP_SQL\" | psql -f - && exit 0; sleep 2; done; exit 1",
  ]

  lifecycle {
    postcondition {
      condition     = self.exit_code == 0
      error_message = "Роль replicator не заведена — деплой остановлен до старта реплики."
    }

    # Сменившийся пароль или SQL должны дойти до базы и тогда, когда сам
    # контейнер не менялся (образ postgres закреплён). Заново заведённая
    # основная база роли не знает — пересоздаём и тогда.
    replace_triggered_by = [
      terraform_data.replication_setup_inputs,
      docker_container.postgres.id,
      docker_volume.postgres.id,
    ]
  }
}

resource "terraform_data" "replication_setup_inputs" {
  input = sha256(join("\n", [
    var.postgres_replication_password,
    file("${path.module}/postgres/replication-setup.sql"),
  ]))
}

# Горячая реплика: физическая потоковая репликация с основного сервера по слоту
# dmc268_replica. Только чтение; приложение в неё не ходит, с неё снимаются
# резервные копии. Защищает от сбоя процесса или тома основной базы, но не от
# падения сервера — реплика на том же хосте (решение задачи: prod — нынешний VPS).
resource "docker_container" "postgres_replica" {
  name    = "dmc268-postgres-replica"
  image   = docker_image.postgres.image_id
  restart = "unless-stopped"

  # Как у основного сервера: entrypoint стартует от root и сам переходит на
  # пользователя postgres. init — чтобы SIGTERM при деплое доходил до процесса
  # и во время пересборки, пока PID 1 был бы sh, игнорирующий сигналы.
  security_opts = ["no-new-privileges:true"]
  init          = true
  memory        = var.postgres_replica_memory
  memory_swap   = var.postgres_replica_memory
  log_driver    = "json-file"
  log_opts      = local.log_opts

  depends_on = [docker_container.replication_setup]

  networks_advanced {
    name = docker_network.backend.name
  }

  env = [
    "PRIMARY_HOST=dmc268-postgres",
    "REPLICATION_PASSWORD=${var.postgres_replication_password}",
  ]

  entrypoint = ["sh", "/opt/dmc268/replica-entrypoint.sh"]
  # hot_standby_feedback — чтобы долгий pg_dump на реплике не обрывался
  # конфликтом с очисткой на основном сервере.
  command = [
    "postgres",
    "-c", "hba_file=/etc/postgresql/pg_hba.conf",
    "-c", "hot_standby_feedback=on",
  ]

  upload {
    file    = "/opt/dmc268/replica-entrypoint.sh"
    content = file("${path.module}/postgres/replica-entrypoint.sh")
  }

  upload {
    file    = "/etc/postgresql/pg_hba.conf"
    content = file("${path.module}/postgres/pg_hba.conf")
  }

  volumes {
    volume_name    = docker_volume.postgres_replica.name
    container_path = "/var/lib/postgresql"
  }

  # Здорова — значит получает поток. Отставшая реплика отвечает на запросы и
  # остаётся в режиме восстановления, но её данные замерли на моменте обрыва:
  # такая реплика должна быть unhealthy, а не выглядеть рабочей.
  healthcheck {
    test     = ["CMD-SHELL", "[ \"$(psql -U ${var.postgres_user} -d ${var.postgres_db} -XtAc 'select status from pg_stat_wal_receiver')\" = streaming ]"]
    interval = "30s"
    timeout  = "5s"
    retries  = 5
    # Первая сборка копирует базу целиком.
    start_period = "5m0s"
  }

  # Заново заведённая основная база — другой кластер: перезапуск контейнера
  # даёт entrypoint заметить это и пересобрать реплику. Сам по себе Terraform
  # реплику бы не тронул — её конфигурация не менялась.
  lifecycle {
    replace_triggered_by = [docker_volume.postgres.id]
  }
}

# Резервные копии: pg_dump раз в сутки — с реплики, а если она не получает
# поток, с основного сервера; хранение — backup_keep_days.
resource "docker_container" "postgres_backup" {
  name    = "dmc268-postgres-backup"
  image   = docker_image.postgres.image_id
  restart = "unless-stopped"

  # Скрипт — аргументом sh -c, а не файлом: upload в контейнер с файловой
  # системой только для чтения Docker не пускает.
  entrypoint = ["sh", "-c", file("${path.module}/postgres/backup.sh")]

  # Свой скрипт, своя роль: root только как владелец тома с копиями, без
  # capabilities и с файловой системой образа только для чтения.
  read_only     = true
  tmpfs         = { "/tmp" = "rw,noexec,nosuid,size=16m" }
  security_opts = ["no-new-privileges:true"]
  memory        = 256
  memory_swap   = 256
  log_driver    = "json-file"
  log_opts      = local.log_opts

  capabilities {
    drop = ["ALL"]
  }

  depends_on = [docker_container.postgres_replica]

  networks_advanced {
    name = docker_network.backend.name
  }

  env = [
    "REPLICA_HOST=dmc268-postgres-replica",
    "PRIMARY_HOST=dmc268-postgres",
    "PGUSER=${var.postgres_user}",
    "PGPASSWORD=${var.postgres_password}",
    "PGDATABASE=${var.postgres_db}",
    "BACKUP_KEEP_DAYS=${var.backup_keep_days}",
    "BACKUP_INTERVAL_SECONDS=86400",
  ]

  volumes {
    volume_name    = docker_volume.postgres_backups.name
    container_path = "/backups"
  }

  # Живость копирования — свежая копия, а не живой процесс: цикл может крутить
  # ошибки pg_dump бесконечно. Порог — сутки плюс два часа запаса.
  healthcheck {
    test         = ["CMD-SHELL", "find /backups -name '*.dump' -mmin -1560 | grep -q ."]
    interval     = "10m0s"
    timeout      = "10s"
    retries      = 1
    start_period = "15m0s"
  }
}
