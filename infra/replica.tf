# Реплика и резервные копии PostgreSQL: роль репликации, горячая реплика и
# ежесуточный pg_dump (спека backend-delivery). Скрипты и конфигурация,
# которые загружаются в контейнеры, — в infra/postgres/.

# Свой том у реплики: это полная физическая копия основного каталога данных.
# Удалённый том реплика пересоберёт сама (pg_basebackup при пустом каталоге).
# prevent_destroy — как у тома postgres (infra/postgres.tf): при потере тома
# основной базы реплика может оказаться последней копией данных. Ручная
# пересборка (`docker volume rm`, infra/README.md) идёт мимо OpenTofu и им не
# блокируется.
resource "docker_volume" "postgres_replica" {
  name = "dmc268-postgres-replica-data"

  lifecycle {
    prevent_destroy = true
  }
}

# Резервные копии лежат на том же диске сервера: от сбоя диска или сервера
# они не спасают, только от ошибочного удаления данных и испорченной базы.
# Вынос копий за пределы сервера — отдельная задача. prevent_destroy — как у
# тома postgres: копии существуют только здесь.
resource "docker_volume" "postgres_backups" {
  name = "dmc268-postgres-backups"

  lifecycle {
    prevent_destroy = true
  }
}

# Роль replicator для реплики. Одноразовый контейнер: отрабатывает и
# завершается, а постусловие роняет apply на ненулевом коде. init-скрипты
# образа на уже заведённом томе не выполняются, поэтому роль заводится здесь,
# идемпотентно. Запускается не на каждом деплое, а когда
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

  # depends_on не ждёт готовности базы (`wait` у postgres срабатывает только
  # при его создании), поэтому повторяем.
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

  # Как у основного сервера: SIGTERM и штатная остановка при пересоздании и
  # docker stop, а не SIGKILL (postgres.tf).
  stop_timeout          = 60
  destroy_grace_seconds = 60

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

  # Намеренно без replace_triggered_by от тома основной базы: заново заведённая
  # основная база может быть пустой, а реплика — последней копией данных.
  # Пересоздавать её автоматически не за чем: на другом кластере entrypoint
  # всё равно остановится и ничего не сотрёт (infra/README.md).
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
  # init — чтобы SIGTERM при деплое останавливал pg_dump, а не ждал SIGKILL:
  # sh как PID 1 сигналы игнорирует. Недописанная копия — *.partial, её
  # уберёт следующий старт.
  init        = true
  memory      = 256
  memory_swap = 256
  log_driver  = "json-file"
  log_opts    = local.log_opts

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
