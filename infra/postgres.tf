# PostgreSQL: образ, том с данными и контейнер.

# Имя со сборкой образа: при переходе с postgres:18-alpine (musl) на
# pgvector/pgvector:pg18 (glibc) том заведён заново — библиотеки сортируют
# строки по-разному, и текстовые индексы старого тома были бы неконсистентны.
#
# prevent_destroy: план, который уничтожает или заменяет том, OpenTofu
# отклоняет до изменений — и полный apply, и targeted apply шага «Поднять базу
# и брокер», который тянет том зависимостью контейнера (спека backend-delivery,
# «Развёртывание не уничтожает тома данных»). Сменить том — новым ресурсом, а
# прежний снять с учёта блоком removed { lifecycle { destroy = false } }; снести
# стенд осознанно — снять prevent_destroy (README.md, «Развёртывание и удаление»).
resource "docker_volume" "postgres" {
  name = "dmc268-postgres-pgvector-data"

  lifecycle {
    prevent_destroy = true
  }
}

resource "docker_image" "postgres" {
  # Образ проекта pgvector поверх официального postgres:18 (Debian, glibc).
  # Взят под RAG-профиль репозитория (#27); тот закрыт без мёржа, и расширение
  # `vector` сейчас не создаёт ни одна миграция. Образ остаётся: возврат на
  # postgres:18-alpine — снова смена библиотеки C и новый том (backend-delivery).
  # Тег плавающий: digest в name пересоздаёт контейнер при каждом обновлении
  # образа, а замену базы деплой отклоняет (сторож плана, #213) — обновление
  # образа шло бы только вручную (#150, #102).
  name         = "pgvector/pgvector:pg18"
  keep_locally = true
}

resource "docker_container" "postgres" {
  name    = "dmc268-postgres"
  image   = docker_image.postgres.image_id
  restart = "unless-stopped"

  # Только запрет повышения привилегий: entrypoint образа стартует от root,
  # делает chown каталога данных и сам переходит на пользователя postgres.
  # Сброс capabilities или user здесь ломают этот старт.
  # Без destroy_grace_seconds провайдер при удалении контейнера шлёт стоп-сигнал
  # образа (SIGINT, fast shutdown) и сразу SIGKILL: база падает без
  # контрольной точки и при старте проходит восстановление по WAL. С ним —
  # ожидание остановки до 60 с: fast shutdown откатывает транзакции и пишет
  # контрольную точку, на мелких данных стенда это секунды. Деплой контейнер
  # пересоздаёт только с allow_stateful_replace (infra.sh). Добавление атрибута —
  # обновление на месте, не замена (проверено на OpenTofu 1.13.2, провайдер 4.6.0).
  destroy_grace_seconds = 60

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

  # Миграции идут сразу после `tofu apply -target` базы (deploy.yml): на
  # чистом сервере apply возвращается, только когда healthcheck зелёный, и
  # миграциям не нужен цикл повторов. На живом стенде wait ничего не стоит:
  # провайдер ждёт только что созданный контейнер.
  wait = true

  healthcheck {
    test     = ["CMD-SHELL", "pg_isready -U ${var.postgres_user} -d ${var.postgres_db}"]
    interval = "10s"
    timeout  = "5s"
    retries  = 5
  }
}
