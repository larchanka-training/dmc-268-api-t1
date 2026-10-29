#!/usr/bin/env bash
# Локальный запуск против настоящего GitHub (make run-local, make stop-local).
#
#   scripts/run_local.sh start   # проверить и поднять всё: Docker, .env, Ollama и модель, база,
#                                # брокер, миграции, API, воркер, туннель
#   scripts/run_local.sh stop    # погасить только то, что запустил start
#
# Чего нет и что не поднять самому (инструмент не установлен, значения GitHub App не заданы) -
# start перечисляет разом, с подсказкой, и выходит с кодом 1; .env без значений создаёт сам.
#
# Логи и PID-файлы - в .local/ (не коммитится). Каждый процесс запускается своей группой
# процессов: stop гасит группу целиком (uv, uvicorn, python), а не только родителя.
# Настройки процессы берут из окружения и .env, как make run-api; секреты в командную
# строку не попадают.
#
# Переменные: TUNNEL (0 - без cloudflared), API_PORT (по умолчанию 8000); от make -
# MAKE, LOCAL_MODEL (модель) и OLLAMA_URL (адрес Ollama).

set -euo pipefail

STATE_DIR=.local
API_PORT="${API_PORT:-8000}"
API_URL="http://localhost:$API_PORT"
TUNNEL="${TUNNEL:-1}"
MAKE="${MAKE:-make}"
LOCAL_MODEL="${LOCAL_MODEL:-qwen2.5-coder:7b}"
OLLAMA_URL="${OLLAMA_URL:-http://localhost:11434}"
# Процессы в порядке запуска; гасим в обратном.
SERVICES=(api worker tunnel)

if [[ -t 1 ]]; then
	GREEN=$'\033[32m' RED=$'\033[31m' YELLOW=$'\033[33m' BOLD=$'\033[1m' DIM=$'\033[2m' RESET=$'\033[0m'
else
	GREEN='' RED='' YELLOW='' BOLD='' DIM='' RESET=''
fi

ok() { printf '  %s✓%s %s\n' "$GREEN" "$RESET" "$1"; }
warn() { printf '  %s!%s %s\n' "$YELLOW" "$RESET" "$1"; }
die() {
	printf '  %s✗%s %s\n' "$RED" "$RESET" "$1" >&2
	exit 1
}

pid_file() { printf '%s/%s.pid' "$STATE_DIR" "$1"; }
log_file() { printf '%s/%s.log' "$STATE_DIR" "$1"; }

# PID процесса, если он запущен нами и ещё жив.
running_pid() {
	local file pid
	file=$(pid_file "$1")
	[[ -f "$file" ]] || return 1
	pid=$(<"$file")
	[[ -n "$pid" ]] && kill -0 "$pid" 2>/dev/null && printf '%s' "$pid"
}

# Запустить команду в фоне своей группой процессов: PGID = PID.
launch() {
	local name=$1
	shift
	set -m
	nohup "$@" >"$(log_file "$name")" 2>&1 &
	echo $! >"$(pid_file "$name")"
	set +m
}

# Показать хвост лога упавшего процесса и выйти.
fail_with_log() {
	local name=$1 reason=$2
	printf '\n%s--- последние строки %s ---%s\n' "$DIM" "$(log_file "$name")" "$RESET" >&2
	tail -n 30 "$(log_file "$name")" >&2 || true
	die "$reason. Остановить остальное: make stop-local"
}

step() { printf '\n%s%s%s\n' "$BOLD" "$1" "$RESET"; }

# Docker установлен, но не запущен: на macOS поднимаем Docker Desktop и ждём демона.
ensure_docker() {
	command -v docker >/dev/null || return 0 # отсутствие покажет проверка окружения
	docker info >/dev/null 2>&1 && return 0
	if [[ "$(uname -s)" == Darwin && -d /Applications/Docker.app ]]; then
		printf '  Docker не запущен - запускаю Docker Desktop\n'
		open -a Docker
		local i
		for i in $(seq 1 90); do
			docker info >/dev/null 2>&1 && { ok "Docker запущен"; return 0; }
			sleep 2
		done
		warn "Docker Desktop не ответил за 3 минуты"
	fi
	# Дальше разберётся проверка окружения: она покажет «Docker не отвечает» с подсказкой.
}

prepare() {
	step "Окружение"
	command -v uv >/dev/null || die "uv не установлен: curl -LsSf https://astral.sh/uv/install.sh | sh  (или brew install uv)"
	scripts/init_local_env.sh
	ensure_docker
	if ! uv run python -m scripts.check_local_env; then
		printf '\nИсправьте отмеченное ✗ (подробно - docs/LOCAL_RUN.md) и снова: make run-local\n' >&2
		exit 1
	fi
	step "Ollama и модель $LOCAL_MODEL"
	"$MAKE" --no-print-directory ollama-up ollama-pull MODEL="$LOCAL_MODEL" OLLAMA_URL="$OLLAMA_URL"
	step "PostgreSQL, RabbitMQ и миграции"
	"$MAKE" --no-print-directory migrate
	step "Процессы"
}

start_api() {
	if curl -s --max-time 2 -o /dev/null "$API_URL/"; then
		die "порт $API_PORT уже занят: другой API? (docker compose stop api worker или make stop-local)"
	fi
	launch api uv run uvicorn app.main:app --port "$API_PORT"
	local i
	for i in $(seq 1 120); do
		if curl -sf --max-time 2 "$API_URL/health" >/dev/null; then
			ok "API отвечает на $API_URL ${DIM}(лог $(log_file api))${RESET}"
			return
		fi
		running_pid api >/dev/null || fail_with_log api "API завершился при старте"
		sleep 1
	done
	fail_with_log api "API не ответил на /health за 120 с"
}

start_worker() {
	launch worker uv run python -m app.worker
	# Ошибки конфигурации воркер выдаёт в первые секунды: ждём их, прежде чем рапортовать.
	# Трейсбек сам по себе не падение: обработанные ошибки (например, сбой повтора доставок)
	# воркер пишет со стектрейсом и работает дальше. Падение - выход процесса или WorkerConfigError.
	local i
	for i in $(seq 1 10); do
		running_pid worker >/dev/null || fail_with_log worker "воркер завершился при старте"
		if grep -q 'WorkerConfigError' "$(log_file worker)"; then
			fail_with_log worker "воркер упал при старте"
		fi
		sleep 1
	done
	ok "воркер запущен, модель ${OLLAMA_MODEL:-из .env} ${DIM}(лог $(log_file worker))${RESET}"
}

start_tunnel() {
	if [[ "$TUNNEL" == 0 ]]; then
		warn "туннель выключен (TUNNEL=0): GitHub не достучится до вебхука"
		return
	fi
	if ! command -v cloudflared >/dev/null; then
		warn "cloudflared не установлен - туннеля нет, GitHub не достучится до вебхука"
		printf '    %sbrew install cloudflared, затем make stop-local && make run-local%s\n' "$DIM" "$RESET"
		return
	fi
	launch tunnel cloudflared tunnel --no-autoupdate --url "$API_URL"
	local i url
	for i in $(seq 1 30); do
		url=$(grep -oE 'https://[a-z0-9-]+\.trycloudflare\.com' "$(log_file tunnel)" | head -n 1 || true)
		if [[ -n "$url" ]]; then
			echo "$url" >"$STATE_DIR/tunnel.url"
			ok "туннель $url ${DIM}(лог $(log_file tunnel))${RESET}"
			return
		fi
		running_pid tunnel >/dev/null || fail_with_log tunnel "cloudflared завершился при старте"
		sleep 1
	done
	fail_with_log tunnel "cloudflared не выдал адрес за 30 с"
}

summary() {
	local tunnel_url=''
	[[ -f "$STATE_DIR/tunnel.url" ]] && running_pid tunnel >/dev/null && tunnel_url=$(<"$STATE_DIR/tunnel.url")
	printf '\n%sБэкенд запущен.%s\n' "$BOLD" "$RESET"
	printf '  Swagger UI:     %s/docs\n' "$API_URL"
	printf '  Вход в GitHub:  %s/api/v1/auth/github/login\n' "$API_URL"
	if [[ -n "$tunnel_url" ]]; then
		printf '  %sWebhook URL для настроек GitHub App:%s %s/api/v1/webhooks/github\n' "$BOLD" "$RESET" "$tunnel_url"
		printf '  %sАдрес туннеля новый при каждом запуске - обновите его в App.%s\n' "$DIM" "$RESET"
	fi
	printf '  Логи:           tail -F %s/*.log\n' "$STATE_DIR"
	printf '  Остановить:     make stop-local\n'
}

cmd_start() {
	mkdir -p "$STATE_DIR"
	local name
	for name in "${SERVICES[@]}"; do
		if running_pid "$name" >/dev/null; then
			die "$name уже запущен (make stop-local, затем повторите)"
		fi
	done
	rm -f "$STATE_DIR/tunnel.url"
	prepare
	# Модель и адрес Ollama - переменными, а не правкой .env: тот же .env читает docker compose.
	export LLM_BACKEND=ollama OLLAMA_BASE_URL="$OLLAMA_URL" OLLAMA_MODEL="$LOCAL_MODEL"
	# Воркер стартует после здорового API: миграции к этому моменту уже прошли.
	start_api
	start_worker
	start_tunnel
	summary
}

stop_one() {
	local name=$1 pid i
	if ! pid=$(running_pid "$name"); then
		rm -f "$(pid_file "$name")"
		return
	fi
	kill -TERM -- "-$pid" 2>/dev/null || kill -TERM "$pid" 2>/dev/null || true
	for i in $(seq 1 15); do
		kill -0 "$pid" 2>/dev/null || break
		sleep 1
	done
	if kill -0 "$pid" 2>/dev/null; then
		kill -KILL -- "-$pid" 2>/dev/null || kill -KILL "$pid" 2>/dev/null || true
		warn "$name не завершился за 15 с - остановлен принудительно"
	fi
	rm -f "$(pid_file "$name")"
	ok "$name остановлен"
}

cmd_stop() {
	local i
	for ((i = ${#SERVICES[@]} - 1; i >= 0; i--)); do
		stop_one "${SERVICES[i]}"
	done
	rm -f "$STATE_DIR/tunnel.url"
	printf '  %sБаза, брокер и Ollama работают дальше: make infra-down - остановить контейнеры.%s\n' "$DIM" "$RESET"
}

case "${1:-}" in
	start) cmd_start ;;
	stop) cmd_stop ;;
	*)
		echo "использование: $0 start|stop" >&2
		exit 2
		;;
esac
