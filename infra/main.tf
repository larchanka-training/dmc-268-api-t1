# Ротация логов: без неё json-file растёт, пока не кончится диск сервера.
# Три файла по 10 МБ на контейнер хватает, чтобы разобрать падение после деплоя.
locals {
  log_opts = {
    "max-size" = "10m"
    "max-file" = "3"
  }
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

# Имя с версией: при переходе с 3.13 на 4.3 том заведён заново, потому что
# прыжок через минорные версии ломает классические очереди с данными.
# Следующее обновление, когда в брокере будут данные, — по шагам через каждую
# минорную версию на этом же томе.
resource "docker_volume" "rabbitmq" {
  name = "dmc268-rabbitmq-v4-data"
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

  env = [
    "DATABASE_URL=postgresql+psycopg://${urlencode(var.postgres_user)}:${urlencode(var.postgres_password)}@dmc268-postgres:5432/${var.postgres_db}",
  ]

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

  env = [
    "DATABASE_URL=postgresql+psycopg://${urlencode(var.postgres_user)}:${urlencode(var.postgres_password)}@dmc268-postgres:5432/${var.postgres_db}",
  ]

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
