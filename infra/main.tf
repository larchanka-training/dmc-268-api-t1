resource "docker_network" "dmc268" {
  name = "dmc268"
}

resource "docker_volume" "postgres" {
  name = "dmc268-postgres-data"
}

resource "docker_volume" "rabbitmq" {
  name = "dmc268-rabbitmq-data"
}

resource "docker_volume" "redis" {
  name = "dmc268-redis-data"
}

resource "docker_image" "postgres" {
  name         = "postgres:16-alpine"
  keep_locally = true
}

resource "docker_image" "rabbitmq" {
  name         = "rabbitmq:3.13-alpine"
  keep_locally = true
}

resource "docker_image" "redis" {
  name         = "redis:7-alpine"
  keep_locally = true
}

resource "docker_container" "postgres" {
  name  = "dmc268-postgres"
  image = docker_image.postgres.image_id

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
    ip       = var.bind_ip
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
  name  = "dmc268-rabbitmq"
  image = docker_image.rabbitmq.image_id

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
    ip       = var.bind_ip
  }

  volumes {
    volume_name    = docker_volume.rabbitmq.name
    container_path = "/var/lib/rabbitmq"
  }
}

resource "docker_container" "redis" {
  name  = "dmc268-redis"
  image = docker_image.redis.image_id

  networks_advanced {
    name = docker_network.dmc268.name
  }

  command = [
    "redis-server",
    "--requirepass",
    var.redis_password,
  ]

  ports {
    internal = 6379
    external = var.redis_port
    ip       = var.bind_ip
  }

  volumes {
    volume_name    = docker_volume.redis.name
    container_path = "/data"
  }
}
