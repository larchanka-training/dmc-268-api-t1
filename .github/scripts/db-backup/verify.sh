#!/usr/bin/env bash
# Проверка вынесенной копии восстановлением (db-backup.yml): расшифровать,
# pg_restore в чистую базу, сверить, что это наша база. Копия, которую нельзя
# восстановить, — не копия, и узнать это надо сейчас, а не в день аварии.
#
# Хранилища скрипт не знает: файл уже скачан шагом workflow.
#
# Аргумент — <имя>.dump.enc. Окружение: STATE_BACKUP_KEY — ключ (параметры
# openssl — как в pull.sh и recover-state.sh); PG_CONTAINER — контейнер чистого
# PostgreSQL 18 (сервис job'а); PGUSER, PGDATABASE — роль и база в нём.
# pg_restore и psql — из этого контейнера: так версия клиента всегда совпадает
# с сервером, какой бы клиент ни стоял в образе раннера. Расшифровка идёт
# трубой в pg_restore — открытая копия на диск не пишется.
set -euo pipefail

file="${1:?зашифрованная копия}"
: "${PG_CONTAINER:?контейнер PostgreSQL}" "${PGUSER:?роль}" "${PGDATABASE:?база}"

if [ -z "${STATE_BACKUP_KEY:-}" ]; then
  echo "::error::нет ключа STATE_BACKUP_KEY (секрет STATE_BACKUP_KEY_DMC268_T1): копию не расшифровать"
  exit 1
fi

# --no-owner и --no-acl: ролей стенда (dmc268_monitor) в чистом кластере нет,
# а данные проверке нужны без них. --exit-on-error — любая ошибка роняет шаг.
openssl enc -d -aes-256-cbc -pbkdf2 -iter 600000 -pass env:STATE_BACKUP_KEY -in "$file" \
  | docker exec -i "$PG_CONTAINER" pg_restore -U "$PGUSER" -d "$PGDATABASE" \
      --no-owner --no-acl --exit-on-error

# Это наша база, а не пустая: одна ревизия Alembic, основные таблицы на месте,
# подключённые репозитории есть (на стенде они есть всегда; пустая таблица —
# признак, что копируется пустая база).
docker exec -i "$PG_CONTAINER" psql -U "$PGUSER" -d "$PGDATABASE" -XtA -v ON_ERROR_STOP=1 <<'SQL'
DO $$
BEGIN
  IF (SELECT count(*) FROM alembic_version) <> 1 THEN
    RAISE EXCEPTION 'в alembic_version не одна ревизия';
  END IF;
  -- Нет таблицы - ошибка самого запроса, ON_ERROR_STOP роняет проверку.
  PERFORM FROM merge_requests, review_runs, findings, users LIMIT 0;
  IF NOT EXISTS (SELECT FROM repositories) THEN
    RAISE EXCEPTION 'таблица repositories пуста';
  END IF;
END $$;
-- Лог прогона публичный: числа строк в него не пишем.
SELECT 'ревизия ' || version_num FROM alembic_version;
SQL
