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

locals {
  api_source_hash = sha256(join("", [
    filesha256("${path.module}/../Dockerfile"),
    filesha256("${path.module}/../pyproject.toml"),
    filesha256("${path.module}/../uv.lock"),
    filesha256("${path.module}/../.dockerignore"),
    sha256(join("", [for f in sort(fileset("${path.module}/../app", "**/*")) : filesha256("${path.module}/../app/${f}")])),
  ]))
  api_image_tag = substr(local.api_source_hash, 0, 12)
}

resource "docker_image" "api" {
  name = "dmc268-api:${local.api_image_tag}"

  build {
    context    = "${path.module}/.."
    dockerfile = "Dockerfile"
    tag        = ["dmc268-api:${local.api_image_tag}"]
    build_args = {
      PYTHON_VERSION = var.python_version
    }

    triggers = {
      source_hash = local.api_source_hash
    }
  }
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
    volume_name    = docker_volume.postgres.name
    container_path = "/var/lib/postgresql/data"
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

resource "docker_container" "api" {
  name    = "dmc268-api"
  image   = docker_image.api.image_id
  restart = "unless-stopped"

  depends_on = [
    docker_container.postgres,
    docker_container.rabbitmq,
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
}
