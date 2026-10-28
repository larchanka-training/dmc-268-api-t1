#!/usr/bin/env bash
# Проверка окружения песочницы воркера (make sandbox-doctor).
# Ничего не меняет: только смотрит и подсказывает, как исправить.
# Проверяет всё до конца, чтобы показать все проблемы разом; код 1 - есть ошибка.
#
# Переменные: OLLAMA_URL (адрес Ollama), MODEL (модель для прогона).

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
	ok "ollama $(ollama --version 2>/dev/null | awk '{print $NF}')"
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

# df -k одинаково работает на macOS и Linux: 4-я колонка - свободные килобайты.
free_kb=$(df -k . | awk 'NR == 2 {print $4}')
if [[ -n "$free_kb" ]] && ((free_kb < MIN_FREE_GB * 1024 * 1024)); then
	warn "на диске меньше ${MIN_FREE_GB} ГБ - модели может не хватить места" "освободите место или возьмите модель меньше: MODEL=qwen2.5-coder:0.5b"
else
	ok "свободного места достаточно"
fi

if ((errors > 0)); then
	printf '\n%sОкружение не готово: ошибок - %d.%s Исправьте отмеченное ✗ и повторите.\n' "$RED" "$errors" "$RESET"
	exit 1
fi
printf '\n%sОкружение готово.%s\n' "$GREEN" "$RESET"
