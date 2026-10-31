#!/usr/bin/env bash
# Проверка окружения песочницы tree-sitter (make sandbox-outline-demo).
# Ничего не меняет: только смотрит и подсказывает, как исправить.
# Проверяет всё до конца, чтобы показать все проблемы разом; код 1 - есть ошибка.
#
# Без аргументов - инструменты: корень репозитория, uv, Python 3.14, git. Ollama
# песочнице tree-sitter не нужна и не проверяется.
# --grammars - после uv sync: версии tree-sitter и грамматик, разбор образца на
# каждый язык (tests/sandbox/outline/grammars.py). Окружение не синхронизирует.
# Функции ok/warn/fail и цвета - свои: общей библиотеки у скриптов нет.

set -u

if [[ -t 1 ]]; then
	GREEN=$'\033[32m' RED=$'\033[31m' YELLOW=$'\033[33m' DIM=$'\033[2m' RESET=$'\033[0m'
else
	GREEN='' RED='' YELLOW='' DIM='' RESET=''
fi

errors=0
ok() { printf '  %s✓%s %s\n' "$GREEN" "$RESET" "$1"; }
warn() { printf '  %s!%s %s\n' "$YELLOW" "$RESET" "$1"; [[ $# -gt 1 ]] && printf '    %s%s%s\n' "$DIM" "$2" "$RESET"; }
fail() { printf '  %s✗%s %s\n' "$RED" "$RESET" "$1"; printf '    %s%s%s\n' "$DIM" "$2" "$RESET"; errors=$((errors + 1)); }

if [[ ! -f pyproject.toml || ! -d tests/sandbox/outline ]]; then
	fail "запуск не из корня репозитория" "cd в корень dmc-268-api-t1 и повторите"
	exit 1
fi

case "${1:-}" in
	"" | --grammars) ;;
	*) fail "неизвестный аргумент $1" "без аргументов или --grammars"; exit 1 ;;
esac

if [[ "${1:-}" == "--grammars" ]]; then
	if ! command -v uv >/dev/null; then
		fail "uv не установлен" "curl -LsSf https://astral.sh/uv/install.sh | sh  (или brew install uv)"
		exit 1
	fi
	# uv run создал бы .venv и мог бы скачать Python даже с --no-sync, а doctor ничего
	# не меняет: без готового окружения - сразу подсказка.
	if [[ ! -x "${UV_PROJECT_ENVIRONMENT:-.venv}/bin/python" ]]; then
		fail "окружение Python не создано" "запустите uv sync --all-extras"
		exit 1
	fi
	# --no-sync: проверяется то, что уже установлено, без синхронизации.
	if ! uv run --no-sync python -c 'import tree_sitter, yaml' >/dev/null 2>&1; then
		fail "окружение Python не готово: нет tree-sitter или зависимостей тестов" "запустите uv sync --all-extras"
		exit 1
	fi
	uv run --no-sync python -m tests.sandbox.outline.grammars
	exit $?
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

if command -v git >/dev/null; then ok "git"; else fail "git не установлен" "поставьте git пакетным менеджером системы"; fi

if ((errors > 0)); then
	printf '\n%sОкружение не готово: ошибок - %d.%s Исправьте отмеченное ✗ и повторите.\n' "$RED" "$errors" "$RESET"
	exit 1
fi
printf '\n%sОкружение готово.%s\n' "$GREEN" "$RESET"
