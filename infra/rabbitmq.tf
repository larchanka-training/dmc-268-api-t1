# RabbitMQ: образ, том с данными и контейнер.

# Имя с версией: при переходе с 3.13 на 4.3 и с 4.3 на 4.4 том заведён
# заново. Первый раз — потому что прыжок через минорные версии ломает
# классические очереди с данными, второй — потому что 4.4 пишет хранилище
# сообщений classic-очередей в новом формате, и откатить узел на 4.3 уже
# нельзя; данных, которые стоило бы переносить, в брокере не было.
# Следующее обновление, когда в брокере будут данные, — по шагам через каждую
# минорную версию на этом же томе, после `rabbitmqctl enable_feature_flag all`.
#
# prevent_destroy — как у тома postgres (infra/postgres.tf).
resource "docker_volume" "rabbitmq" {
  name = "dmc268-rabbitmq-v44-data"

  lifecycle {
    prevent_destroy = true
  }
}

resource "docker_image" "rabbitmq" {
  # Тег плавающий по той же причине, что у postgres: digest — после
  # destroy_grace_seconds у брокера (#150, #102).
  name         = "rabbitmq:4.4-alpine"
  keep_locally = true
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
