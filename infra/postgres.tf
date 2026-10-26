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
  # Тег плавающий: digest в name пересоздавал бы контейнер при каждом
  # обновлении образа (#150). Теперь пересоздание идёт через SIGTERM:
  # остановку при пересоздании обслуживает destroy_grace_seconds контейнера
  # ниже (#102), и digest можно закрепить отдельной задачей.
  name         = "pgvector/pgvector:pg18"
  keep_locally = true
}

resource "docker_container" "postgres" {
  name    = "dmc268-postgres"
  image   = docker_image.postgres.image_id
  restart = "unless-stopped"

  # Библиотека pg_stat_statements нужна ручной диагностике медленных запросов
  # (расширение создаёт миграция 0013); загружаем её только вместе со стеком
  # наблюдаемости — накладные расходы держать не просили. Receiver postgresql
  # коллектора в расширении не нуждается — метрики базы он снимает обычными
  # вьюхами статистики. Смена command пересоздаёт контейнер базы (том
  # сохраняется): это происходит и при переключателе стека, и при смене
  # digest образа. Локальный compose preload держит всегда (накладные расходы
  # ничтожны, переключателя в compose нет).
  #
  # track_utility=off при preload обязателен: с ним утилитные выражения — в
  # том числе `ALTER ROLE … PASSWORD '…'` из replication-setup.sql и ранбуков —
  # не попадают в pg_stat_statements открытым текстом, а роль с pg_monitor
  # статистику читает (замечание ревью PR #130, medium). По той же причине
  # `SET LOCAL track_utility = off` стоит в 0013 и migrate.sh — на случай
  # preload без этого флага (локальный compose).
  #
  # wal_level=replica и max_wal_senders у PostgreSQL 18 уже такие, какие нужны
  # реплике; задаём только то, чего нет по умолчанию. max_slot_wal_keep_size —
  # потолок WAL, который удержит слот реплики: без него упавшая реплика копила
  # бы WAL на основном сервере, пока не кончится диск. Перешагнувшая потолок
  # реплика теряет слот; при следующем старте она заметит это сама и
  # пересоберётся (infra/postgres/replica-entrypoint.sh).
  command = concat([
    "postgres",
    "-c", "hba_file=/etc/postgresql/pg_hba.conf",
    "-c", "max_slot_wal_keep_size=${var.postgres_max_slot_wal_keep_size}",
    ], var.observability_enabled ? [
    "-c", "shared_preload_libraries=pg_stat_statements",
    "-c", "pg_stat_statements.track_utility=off",
  ] : [])

  # Окно остановки: смена command (переключатель observability_enabled или
  # новый digest образа) пересоздаёт контейнер базы, и без окна провайдер
  # делает SIGKILL под пишущей базой (замечание ревью PR #130, high): база
  # поднимается с «not properly shut down» и восстановлением, брокер теряет
  # то, что не успел сбросить на диск. stop_timeout — окно docker stop,
  # destroy_grace_seconds — то же при пересоздании провайдером; 60 с — запас
  # postgres на чекпоинт и чистую остановку.
  stop_timeout          = 60
  destroy_grace_seconds = 60

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
