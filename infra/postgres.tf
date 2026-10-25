# PostgreSQL: образ, том с данными и контейнер.

# Имя со сборкой образа: при переходе с postgres:18-alpine (musl) на
# pgvector/pgvector:pg18 (glibc) том заведён заново — библиотеки сортируют
# строки по-разному, и текстовые индексы старого тома были бы неконсистентны.
resource "docker_volume" "postgres" {
  name = "dmc268-postgres-pgvector-data"
}

resource "docker_image" "postgres" {
  # Образ проекта pgvector поверх официального postgres:18 (Debian, glibc).
  # Взят под RAG-профиль репозитория (#27); тот закрыт без мёржа, и расширение
  # `vector` сейчас не создаёт ни одна миграция. Образ остаётся: возврат на
  # postgres:18-alpine — снова смена библиотеки C и новый том (backend-delivery).
  # Тег плавающий: digest в name пересоздаёт контейнер при каждом обновлении
  # образа, а без destroy_grace_seconds пересоздание — это SIGKILL (#150, #102).
  name         = "pgvector/pgvector:pg18"
  keep_locally = true
}

resource "docker_container" "postgres" {
  name    = "dmc268-postgres"
  image   = docker_image.postgres.image_id
  restart = "unless-stopped"

  # Библиотека pg_stat_statements нужна ручной диагностике медленных запросов
  # (расширение создаёт миграция 0013); загружаем её только вместе со стеком
  # наблюдаемости, при observability_enabled = false command = null возвращает
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
