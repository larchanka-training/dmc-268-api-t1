#!/usr/bin/env bash
# Проверка окружения песочницы воркера (make sandbox-doctor, шаг 1/4 make sandbox-demo).
# Ничего не меняет: только смотрит и подсказывает, как исправить.
# Проверяет всё до конца, чтобы показать все проблемы разом; код 1 - есть ошибка.
#
# Проверяет: корень репозитория, uv, Python 3.14, git, curl, ollama, отвечает ли
# Ollama и скачана ли модель, свободное место там, где Ollama хранит модели
# (OLLAMA_MODELS, по умолчанию ~/.ollama/models). Не запущенная Ollama и не
# скачанная модель - предупреждение, а не ошибка: их поднимает шаг 3/4 sandbox-demo.
# Переменные: OLLAMA_URL (адрес Ollama), MODEL (модель для прогона),
# OLLAMA_MODELS (каталог моделей Ollama).
# Функции ok/warn/fail и цвета - свои: общей библиотеки у скриптов нет.

set -u

OLLAMA_URL="${OLLAMA_URL:-http://localhost:11434}"
MODEL="${MODEL:-qwen2.5-coder:1.5b}"
MIN_FREE_GB=3

if [[ -t 1 ]]; then
	GREEN=$'\033[32m' RED=$'\033[31m' YELLOW=$'\033[33m' DIM=$'\033[2m' RESET=$'\033[0m'
else
	GREEN='' RED='' YELLOW='' DIM='' RESET=''
fi

errors=0
ok() { printf '  %s✓%s %s\n' "$GREEN" "$RESET" "$1"; }
warn() { printf '  %s!%s %s\n' "$YELLOW" "$RESET" "$1"; [[ $# -gt 1 ]] && printf '    %s%s%s\n' "$DIM" "$2" "$RESET"; }
fail() { printf '  %s✗%s %s\n' "$RED" "$RESET" "$1"; printf '    %s%s%s\n' "$DIM" "$2" "$RESET"; errors=$((errors + 1)); }

case "$(uname -s)" in
	Darwin) OLLAMA_HINT='brew install ollama  (или https://ollama.com/download)' ;;
	*) OLLAMA_HINT='curl -fsSL https://ollama.com/install.sh | sh' ;;
esac

if [[ ! -f pyproject.toml || ! -d tests/sandbox ]]; then
	fail "запуск не из корня репозитория" "cd в корень dmc-268-api-t1 и повторите"
	exit 1
fi

if command -v uv >/dev/null; then
	ok "uv $(uv --version | awk '{print $2}')"
	if py=$(uv python find 3.14 2>/dev/null); then
		ok "Python 3.14 ${DIM}($py)${RESET}"
	else
		warn "Python 3.14 не найден - uv скачает его на шаге зависимостей" "или заранее: uv python install 3.14"
	fi
else
	fail "uv не установлен" "curl -LsSf https://astral.sh/uv/install.sh | sh  (или brew install uv)"
fi

for tool in git curl; do
	if command -v "$tool" >/dev/null; then ok "$tool"; else fail "$tool не установлен" "поставьте $tool пакетным менеджером системы"; fi
done

if command -v ollama >/dev/null; then
	# Без сервера `ollama --version` печатает в stdout два предупреждения, версия -
	# в строке «client version is X»; с сервером - «ollama version is X».
	ok "ollama $(ollama --version 2>/dev/null | awk '/version is/ {print $NF; exit}')"
	if curl -sf --max-time 3 "$OLLAMA_URL/api/tags" >/dev/null; then
		ok "Ollama отвечает на $OLLAMA_URL"
		if curl -sf --max-time 3 "$OLLAMA_URL/api/tags" | grep -q "\"name\":\"$MODEL\(:latest\)\{0,1\}\""; then
			ok "модель $MODEL скачана"
		else
			warn "модель $MODEL не скачана - скачается на шаге Ollama"
		fi
	else
		warn "Ollama не отвечает на $OLLAMA_URL - будет запущена на шаге Ollama"
	fi
else
	fail "ollama не установлена" "$OLLAMA_HINT"
fi

# Место смотрим там, куда Ollama скачает модель; каталога может ещё не быть -
# тогда у ближайшего существующего родителя.
dir="${OLLAMA_MODELS:-$HOME/.ollama/models}"
while [[ ! -e $dir ]]; do dir=$(dirname "$dir"); done
# df -k одинаково работает на macOS и Linux: 4-я колонка - свободные килобайты.
free_kb=$(df -k "$dir" 2>/dev/null | awk 'NR == 2 {print $4}')
if [[ ! $free_kb =~ ^[0-9]+$ ]]; then
	warn "не удалось проверить свободное место в $dir"
elif ((free_kb < MIN_FREE_GB * 1024 * 1024)); then
	warn "в $dir меньше ${MIN_FREE_GB} ГБ - модели может не хватить места" "освободите место или возьмите модель меньше: MODEL=qwen2.5-coder:0.5b"
else
	ok "свободного места достаточно ${DIM}($dir)${RESET}"
fi

if ((errors > 0)); then
	printf '\n%sОкружение не готово: ошибок - %d.%s Исправьте отмеченное ✗ и повторите.\n' "$RED" "$errors" "$RESET"
	exit 1
fi
printf '\n%sОкружение готово.%s\n' "$GREEN" "$RESET"
