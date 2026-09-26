resource "docker_network" "dmc268" {
  name = "dmc268"
}

resource "docker_volume" "postgres" {
  name = "dmc268-postgres-data"
}

resource "docker_volume" "rabbitmq" {
  name = "dmc268-rabbitmq-data"
}

resource "docker_image" "postgres" {
  name         = "postgres:18-alpine"
  keep_locally = true
}

resource "docker_image" "rabbitmq" {
  name         = "rabbitmq:3.13-alpine"
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
