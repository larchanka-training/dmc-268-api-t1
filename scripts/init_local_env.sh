#!/usr/bin/env bash
# Подготовить .env для локального запуска против настоящего GitHub (make local-env).
#
# Создаёт .env из .env.example, если его нет, и генерирует USER_TOKEN_ENCRYPTION_KEY, если
# он пуст. Заполненные значения не трогает, ключ не печатает. Модель и адрес Ollama make
# run-local передаёт процессам сам: .env остаётся пригодным и для docker compose.

set -euo pipefail

ENV_FILE=.env

if [[ ! -f .env.example ]]; then
	echo "запуск не из корня репозитория: cd в корень dmc-268-api-t1 и повторите" >&2
	exit 1
fi

if [[ -f "$ENV_FILE" ]]; then
	echo "  $ENV_FILE уже есть - дополняю только пустое"
else
	# Права 600 до записи секретов: файл читает только владелец.
	(umask 077 && cp .env.example "$ENV_FILE")
	echo "  создан $ENV_FILE из .env.example"
fi

if grep -qE '^USER_TOKEN_ENCRYPTION_KEY=.+' "$ENV_FILE"; then
	echo "  USER_TOKEN_ENCRYPTION_KEY уже задан"
else
	key=$(openssl rand -base64 32)
	tmp=$(mktemp "$ENV_FILE.XXXXXX")
	# Ключ уходит в файл через окружение awk, а не аргументом: в выводе и в ps его нет.
	KEY="$key" awk '
		/^USER_TOKEN_ENCRYPTION_KEY=/ { print "USER_TOKEN_ENCRYPTION_KEY=" ENVIRON["KEY"]; done = 1; next }
		{ print }
		END { if (!done) print "USER_TOKEN_ENCRYPTION_KEY=" ENVIRON["KEY"] }
	' "$ENV_FILE" >"$tmp"
	unset key
	chmod 600 "$tmp"
	mv "$tmp" "$ENV_FILE"
	echo "  USER_TOKEN_ENCRYPTION_KEY сгенерирован"
fi

cat <<'EOF'

Заполните в .env своим редактором значения вашей GitHub App (docs/LOCAL_RUN.md):
  GITHUB_APP_ID, GITHUB_APP_CLIENT_ID, GITHUB_APP_CLIENT_SECRET,
  GITHUB_WEBHOOK_SECRET (вместо local-dev-secret),
  GITHUB_APP_PRIVATE_KEY - PEM целиком, в двойных кавычках, с переносами строк:
    GITHUB_APP_PRIVATE_KEY="-----BEGIN RSA PRIVATE KEY-----
    MIIE...
    -----END RSA PRIVATE KEY-----"
Затем: make local-doctor
EOF
