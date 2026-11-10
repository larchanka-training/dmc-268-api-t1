#!/usr/bin/env bash
# Свежая копия базы со стенда — зашифрованным файлом на раннере (db-backup.yml,
# спека backend-delivery, «Резервные копии базы данных хранятся вне сервера»).
#
# Берёт самый новый /backups/*.dump контейнера dmc268-postgres-backup
# (infra/postgres/backup.sh) и шифрует его по трубе: открытая копия на диск
# раннера не пишется. Хранилища скрипт не знает — файл загружает следующий шаг
# workflow, и смена хранилища этот скрипт не трогает.
#
# Ключ — STATE_BACKUP_KEY (секрет STATE_BACKUP_KEY_DMC268_T1), параметры
# openssl — те же, что у копии errored.tfstate в recover-state.sh: один ключ
# открывает обе. Нет ключа — `::error::` и код 1 до чтения копии: открытой она
# не уходит никуда, репозиторий публичный.
#
# Копия моложе 26 часов (1560 мин, как healthcheck контейнера копий в
# infra/replica.tf) — или отказ: старая копия значит, что копирование на
# сервере сломано, и выносить её вместо свежей — прятать поломку.
#
# Аргумент — каталог для файла. Наружу (в GITHUB_OUTPUT, если задан):
# name — имя копии без .dump (dmc268-<UTC>), file — путь к <имя>.dump.enc.
# Нужен хост `stand` в ~/.ssh/config (.github/actions/stand-ssh).
#
# Имя контейнера, порог и имя копии в командах ssh раскрываются на раннере
# намеренно.
# shellcheck disable=SC2029
set -euo pipefail

out_dir="${1:?каталог для зашифрованной копии}"
container=dmc268-postgres-backup
max_age_min=1560

if [ -z "${STATE_BACKUP_KEY:-}" ]; then
  echo "::error::нет ключа STATE_BACKUP_KEY (секрет STATE_BACKUP_KEY_DMC268_T1): копия базы без шифрования не выносится"
  exit 1
fi

# Имя и время изменения самой новой свежей копии. *.partial — недописанные,
# их маска не берёт.
newest="$(ssh stand "docker exec $container find /backups -maxdepth 1 -name '*.dump' -mmin -$max_age_min -printf '%T@ %f\n'" \
  | sort -n | tail -n 1 | cut -d' ' -f2)"
if [ -z "$newest" ]; then
  echo "::error::в $container нет копии моложе $((max_age_min / 60)) ч: резервное копирование на сервере не работает (docker logs $container)"
  exit 1
fi
# Имя идёт в команду ssh — только ожидаемый вид, без кавычек и пробелов.
if ! [[ "$newest" =~ ^[A-Za-z0-9._-]+\.dump$ ]]; then
  echo "::error::неожиданное имя копии: $newest"
  exit 1
fi

name="${newest%.dump}"
file="$out_dir/$newest.enc"
mkdir -p "$out_dir"
(umask 077
  ssh stand "docker exec $container cat /backups/$newest" \
    | openssl enc -aes-256-cbc -pbkdf2 -iter 600000 -salt -pass env:STATE_BACKUP_KEY -out "$file")
echo "копия $newest зашифрована: $(du -h "$file" | cut -f1)"
if [ -n "${GITHUB_OUTPUT:-}" ]; then
  printf 'name=%s\nfile=%s\n' "$name" "$file" >> "$GITHUB_OUTPUT"
fi
