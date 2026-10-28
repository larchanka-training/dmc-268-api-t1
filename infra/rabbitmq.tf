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
  # Тег плавающий по той же причине, что у postgres (#150).
  name         = "rabbitmq:4.4-alpine"
  keep_locally = true
}

resource "docker_container" "rabbitmq" {
  name    = "dmc268-rabbitmq"
  image   = docker_image.rabbitmq.image_id
  restart = "unless-stopped"

  # Без destroy_grace_seconds провайдер при пересоздании удаляет контейнер
  # принудительно (SIGKILL): база поднимается с «not properly shut down» и
  # восстановлением, брокер теряет то, что не успел сбросить на диск. С ним —
  # SIGTERM и ожидание штатной остановки; stop_timeout — то же для docker stop.
  # 60 с хватает с запасом: контрольная точка при остановке на нашем объёме —
  # секунды.
  stop_timeout          = 60
  destroy_grace_seconds = 60

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

  # Как у postgres: пользователя по умолчанию брокер заводит только на пустом
  # томе, правку env контейнер не видит, а пароль выставляет шаг деплоя
  # .github/scripts/deploy/passwords.sh (#161).
  env = [
    "RABBITMQ_DEFAULT_USER=${var.rabbitmq_user}",
    "RABBITMQ_DEFAULT_PASS=${var.rabbitmq_password}",
  ]

  # Смена пароля контейнер не трогает, а смену имени пользователя ignore_changes
  # спрятал бы: шаг паролей сменил бы пароль прежнего пользователя, а API и
  # воркер входили бы новым. Новое имя в заведённом томе и не появится — ни
  # сейчас, ни после замены контейнера. Поэтому plan падает до изменений на
  # сервере: self.env при ignore_changes — то, с чем контейнер создан.
  # replace_triggered_by от terraform_data не годится: первое создание такого
  # ресурса — тоже повод к замене, и переход на эту конфигурацию пересоздал бы
  # брокер (проверено на dind, #161).
  lifecycle {
    ignore_changes = [env]

    postcondition {
      condition     = contains(self.env, "RABBITMQ_DEFAULT_USER=${var.rabbitmq_user}")
      error_message = "rabbitmq_user не совпадает с тем, с которыми заведён контейнер: имена в заведённом томе не меняются. Новое имя — новый том (infra/README.md, «Смена паролей»)."
    }
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
