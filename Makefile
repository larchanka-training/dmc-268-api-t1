# Локальный запуск и проверка компонентов по отдельности.
# `make` или `make help` - список целей. Переменные переопределяются в команде:
#   make sandbox MODEL=qwen2.5-coder:7b CASE=04-users-sql-injection
#   make test-db ARGS="-k migration -x"
# Рецепты без .ONESHELL: на macOS стоит GNU make 3.81.

SHELL := /bin/bash

# --- переменные ---------------------------------------------------------------

MODEL      ?= $(or $(OLLAMA_MODEL),qwen2.5-coder:1.5b)
OLLAMA_URL ?= $(or $(OLLAMA_BASE_URL),http://localhost:11434)
# Кейс корпуса tests/eval/rules/corpus/ без .yaml; пусто - все кейсы.
CASE       ?=
# Содержимое .review/rules.md: absent | rules | empty | adversarial.
RULES      ?= rules
# Сценарий модели и кейс для sandbox-script; диапазон git для sandbox-git.
SCRIPT     ?= tests/sandbox/examples/llm-script.json
CASE_FILE  ?= tests/sandbox/examples/case.yaml
# Мок-PR для sandbox-demo.
DEMO_CASE  ?= tests/sandbox/examples/case.yaml
RANGE      ?= develop..HEAD
# Дополнительные ключи: для sandbox - его флаги, для test-* - флаги pytest.
ARGS       ?=

# База и брокер тестов из docker-compose.yml (порты проброшены на 127.0.0.1).
DB_USER := dmc268
DB_PASS := dmc268
# Отдельная база тестов: подготовка удаляет её схему (см. README, «Тесты»).
TEST_DATABASE_URL ?= postgresql+psycopg://$(DB_USER):$(DB_PASS)@localhost:5432/dmc268_test
# Отдельный vhost тестов: очистка очередей в тестах не трогает очередь стека разработки в `/`.
TEST_RABBITMQ_VHOST := dmc268_test
TEST_RABBITMQ_URL ?= amqp://guest:guest@localhost:5672/$(TEST_RABBITMQ_VHOST)

SANDBOX_FLAGS = --rules $(RULES) $(if $(CASE),--case $(CASE)) $(ARGS)
# Окружение тестов, которым нужны PostgreSQL и RabbitMQ.
INFRA_TEST_ENV = TEST_DATABASE_URL='$(TEST_DATABASE_URL)' \
	TEST_RABBITMQ_URL='$(TEST_RABBITMQ_URL)' \
	TEST_RABBITMQ_CONTAINER="$$(docker compose ps -q rabbitmq)"

# Окружение процессов API и воркера вне Docker. Settings читает и окружение, и .env,
# причём окружение главнее (app/config.py): значение по умолчанию отсюда молча
# перекрыло бы .env. Поэтому процессу уходит только то, что задано явно - в команде
# make или в окружении оболочки; остальное приложение берёт из .env.
# Явно заданная переменная: в команде make или в окружении, а не значение по умолчанию.
explicit = $(filter command line environment environment override,$(origin $(1)))
pass = $(if $(call explicit,$(1)),$(1)='$($(1))')
# GITHUB_WEBHOOK_SECRET в рецепт не пишем, чтобы секрет не печатался: заданный в
# окружении make передаёт процессу сам, иначе он берётся из .env.
SERVICE_ENV = $(call pass,DATABASE_URL) $(call pass,RABBITMQ_URL)
# Воркер: LLM_BACKEND - только явный. Явный LLM_BACKEND=ollama подставляет и адрес
# с моделью песочницы (OLLAMA_URL, MODEL); без него они уходят, только если заданы явно.
OLLAMA_EXPLICIT = $(filter ollama,$(LLM_BACKEND))
WORKER_ENV = $(SERVICE_ENV) $(call pass,LLM_BACKEND) \
	$(if $(or $(OLLAMA_EXPLICIT),$(call explicit,OLLAMA_URL),$(call explicit,OLLAMA_BASE_URL)),OLLAMA_BASE_URL='$(OLLAMA_URL)') \
	$(if $(or $(OLLAMA_EXPLICIT),$(call explicit,MODEL),$(call explicit,OLLAMA_MODEL)),OLLAMA_MODEL='$(MODEL)')

# Версия openspec - та же, что в .github/workflows/ci.yml.
OPENSPEC := npx --yes @fission-ai/openspec@1.15.0

.DEFAULT_GOAL := help
.PHONY: help check \
	sandbox-demo sandbox-doctor sandbox-down sandbox sandbox-stub sandbox-oracle sandbox-script sandbox-git ollama-up ollama-pull \
	test-db test-queue test-e2e test-all \
	infra-up infra-down test-db-create test-queue-vhost migrate run-api run-worker

help: ## Список целей
	@grep -E '^[a-zA-Z0-9_-]+:.*## ' $(MAKEFILE_LIST) | \
		awk 'BEGIN {FS = ":.*## "}; {printf "  \033[36m%-20s\033[0m %s\n", $$1, $$2}'

check: ## Статические проверки как в CI: lock, ruff, lint-imports, mypy, контракт API, openspec
	uv lock --check
	uv run ruff check .
	uv run lint-imports
	uv run mypy .
	uv run python scripts/export_openapi.py --check
	$(OPENSPEC) validate --all --strict
	set -o pipefail; $(OPENSPEC) list --json | python3 scripts/check_openspec_archived.py

# --- песочница воркера: всё подменено, кроме модели ------------------------------

sandbox-demo: ## Начать отсюда: проверить окружение, поднять Ollama и модель, прогнать мок-PR (MODEL, DEMO_CASE)
	@printf '\n\033[1m==> 1/4 Проверка окружения\033[0m\n'
	@OLLAMA_URL='$(OLLAMA_URL)' MODEL='$(MODEL)' scripts/sandbox-doctor.sh
	@printf '\n\033[1m==> 2/4 Зависимости Python (uv sync)\033[0m\n'
	@uv sync --all-extras
	@printf '\n\033[1m==> 3/4 Ollama и модель $(MODEL)\033[0m\n'
	@$(MAKE) --no-print-directory ollama-up ollama-pull
	@printf '\n\033[1m==> 4/4 Прогон воркера на мок-PR $(DEMO_CASE)\033[0m\n'
	@uv run python -m tests.sandbox --llm ollama --model '$(MODEL)' --ollama-url '$(OLLAMA_URL)' \
		--case-file '$(DEMO_CASE)' $(SANDBOX_FLAGS); status=$$?; echo; \
	case $$status in \
		0) printf '\033[32mГотово: прогон completed, сообщение подтверждено (ack).\033[0m\n' ;; \
		1) printf '\033[31mПрогон не завершился или сообщение ушло в nack - см. отчёт выше; подробнее: make sandbox-demo ARGS=-v\033[0m\n' ;; \
		*) printf '\033[31mВход или окружение не подошли (код %s) - причина в строке выше.\033[0m\n' $$status ;; \
	esac; \
	echo "Дальше: другая модель - MODEL=..., свой PR - DEMO_CASE=путь.yaml, остановить Ollama - make sandbox-down."; \
	echo "Подробно: tests/sandbox/README.md"; \
	exit $$status

sandbox-doctor: ## Проверить окружение песочницы, ничего не меняя
	@OLLAMA_URL='$(OLLAMA_URL)' MODEL='$(MODEL)' scripts/sandbox-doctor.sh

sandbox-down: ## Остановить Ollama, если её запустил make ollama-up
	@if [ -f .ollama.pid ] && ps -p "$$(cat .ollama.pid)" -o command= 2>/dev/null | grep -q ollama; then \
		kill "$$(cat .ollama.pid)" && echo "Ollama (pid $$(cat .ollama.pid)) остановлена"; \
	else \
		echo "Ollama, запущенной через make, нет - ничего не делаю"; \
	fi; \
	rm -f .ollama.pid

sandbox: ollama-up ollama-pull ## Воркер на корпусе PR с локальной Ollama (MODEL, CASE, RULES, ARGS)
	uv run python -m tests.sandbox --llm ollama --model '$(MODEL)' --ollama-url '$(OLLAMA_URL)' $(SANDBOX_FLAGS)

sandbox-stub: ## Воркер на корпусе PR без модели, офлайн (CASE, RULES, ARGS)
	uv run python -m tests.sandbox --llm stub $(SANDBOX_FLAGS)

sandbox-oracle: ## Воркер на корпусе PR с оракулом: ровно размеченные нарушения (CASE, RULES, ARGS)
	uv run python -m tests.sandbox --llm oracle $(SANDBOX_FLAGS)

sandbox-script: ## Воркер на CASE_FILE со сценарием модели SCRIPT (RULES, ARGS)
	uv run python -m tests.sandbox --llm script --llm-script '$(SCRIPT)' --case-file '$(CASE_FILE)' $(SANDBOX_FLAGS)

sandbox-git: ## Воркер на диапазоне git RANGE с заглушкой модели (RULES, ARGS)
	uv run python -m tests.sandbox --llm stub --from-git '$(RANGE)' $(SANDBOX_FLAGS)

ollama-up: ## Поднять ollama serve в фоне, если она ещё не отвечает (остановить - make sandbox-down)
	@if curl -sf '$(OLLAMA_URL)/api/tags' >/dev/null; then \
		echo "Ollama уже отвечает на $(OLLAMA_URL)"; \
	else \
		command -v ollama >/dev/null || { echo "ollama не установлена: brew install ollama" >&2; exit 1; }; \
		echo "Запускаю ollama serve в фоне, лог - .ollama.log"; \
		OLLAMA_HOST='$(OLLAMA_URL)' nohup ollama serve >.ollama.log 2>&1 & \
		echo $$! >.ollama.pid; \
		for i in $$(seq 1 30); do curl -sf '$(OLLAMA_URL)/api/tags' >/dev/null && exit 0; sleep 1; done; \
		echo "Ollama не ответила за 30 с, см. .ollama.log" >&2; exit 1; \
	fi

ollama-pull: ## Скачать MODEL, если её ещё нет
	@OLLAMA_HOST='$(OLLAMA_URL)' ollama list | awk 'NR > 1 {print $$1}' | grep -qx -e '$(MODEL)' -e '$(MODEL):latest' \
		&& echo "Модель $(MODEL) уже скачана" \
		|| OLLAMA_HOST='$(OLLAMA_URL)' ollama pull '$(MODEL)'

# --- песочница tree-sitter: контекст модели без модели ----------------------------
# CASE, CASE_FILE, RANGE, RULES и ARGS - общие с песочницей воркера. У CASE_FILE и
# RANGE есть значения по умолчанию, поэтому сюда они уходят, только если заданы явно.
OUTLINE_CASE ?= tests/sandbox/outline/examples/outline-case.yaml
# FILE (свой файл) и LINES (его изменённые строки: N, N-M или список через запятую)
# берутся только из командной строки make: LINES - высота терминала, её экспортируют
# bash, zsh и CI, и из окружения она молча сузила бы диапазон.
cmdline = $(filter command line,$(origin $(1)))
OUTLINE_FLAGS = --rules $(RULES) $(if $(CASE),--case $(CASE)) \
	$(if $(call explicit,CASE_FILE),--case-file '$(CASE_FILE)') \
	$(if $(call explicit,RANGE),--from-git '$(RANGE)') \
	$(if $(call cmdline,FILE),--file '$(FILE)') $(if $(call cmdline,LINES),--lines '$(LINES)') $(ARGS)

.PHONY: sandbox-outline-demo sandbox-outline

sandbox-outline-demo: ## tree-sitter: проверить окружение и грамматики, разобрать мок-PR (OUTLINE_CASE, ARGS)
	@printf '\n\033[1m==> 1/3 Проверка окружения\033[0m\n'
	@scripts/sandbox-outline-doctor.sh
	@printf '\n\033[1m==> 2/3 Зависимости Python (uv sync) и грамматики tree-sitter\033[0m\n'
	@uv sync --all-extras
	@scripts/sandbox-outline-doctor.sh --grammars
	@printf '\n\033[1m==> 3/3 Разбор мок-PR $(OUTLINE_CASE)\033[0m\n'
	@uv run python -m tests.sandbox.outline --rules $(RULES) --case-file '$(OUTLINE_CASE)' $(ARGS); status=$$?; echo; \
	case $$status in \
		0) printf '\033[32mГотово: контекст собран, отчёт выше.\033[0m\n' ;; \
		1) printf '\033[31mЕсть проблемы разбора (parse_error, пустой контур или дифф не получен) - см. «проблемы» в отчёте.\033[0m\n' ;; \
		139) printf '\033[31mSegfault в tree-sitter - версия 0.26.0? См. #226: uv sync --all-extras.\033[0m\n' ;; \
		*) printf '\033[31mВход или окружение не подошли (код %s) - причина в строке выше.\033[0m\n' $$status ;; \
	esac; \
	echo "Дальше: свой файл - make sandbox-outline FILE=app/x.py LINES=10-20, свои коммиты - make sandbox-outline RANGE=develop..HEAD."; \
	echo "Подробно: tests/sandbox/outline/README.md"; \
	exit $$status

sandbox-outline: ## tree-sitter на CASE / CASE_FILE / RANGE / FILE (+ LINES); без входов - мок-PR (RULES, ARGS)
	uv run python -m tests.sandbox.outline $(OUTLINE_FLAGS)

# --- тесты по компонентам: с PostgreSQL и RabbitMQ из compose ----------------------

test-db: infra-up test-db-create ## Адаптеры базы и миграции: tests/db
	$(INFRA_TEST_ENV) uv run pytest tests/db $(ARGS)

test-queue: infra-up test-queue-vhost ## Адаптер RabbitMQ: tests/queue
	$(INFRA_TEST_ENV) uv run pytest tests/queue $(ARGS)

test-e2e: infra-up test-db-create test-queue-vhost ## Сквозной стенд вебхук → воркер → GitHub: tests/e2e
	$(INFRA_TEST_ENV) uv run pytest tests/e2e $(ARGS)

# Как job'ы test и chaos в CI. Chaos-сьют (tests/chaos, маркер chaos) приходит с #225:
# пока каталога нет, второй прогон пропускается с сообщением.
test-all: infra-up test-db-create test-queue-vhost ## Весь набор как в CI, вместе с chaos: пропуск = провал
	PYTEST_FAIL_ON_SKIP=1 $(INFRA_TEST_ENV) uv run pytest -q -rs $(ARGS)
	@if [ -d tests/chaos ]; then \
		echo "PYTEST_FAIL_ON_SKIP=1 ... uv run pytest -m chaos -q -rs tests/chaos"; \
		PYTEST_FAIL_ON_SKIP=1 $(INFRA_TEST_ENV) uv run pytest -m chaos -q -rs tests/chaos; \
	else \
		echo "tests/chaos нет в этой ветке - chaos-сьют пропущен"; \
	fi

# --- инфраструктура и сервисы -----------------------------------------------------

infra-up: ## Поднять PostgreSQL и RabbitMQ из compose и дождаться healthcheck
	docker compose up -d --wait postgres rabbitmq

infra-down: ## Остановить контейнеры compose (данные в томах остаются)
	docker compose down

test-db-create: ## Создать базу dmc268_test, если её нет
	@docker compose exec -T postgres psql -U $(DB_USER) -d dmc268 -tAc \
		"SELECT 1 FROM pg_database WHERE datname = 'dmc268_test'" | grep -q 1 \
		|| docker compose exec -T postgres createdb -U $(DB_USER) dmc268_test

test-queue-vhost: ## Создать vhost dmc268_test брокера для тестов, если его нет
	@docker compose exec -T rabbitmq rabbitmqctl -q list_vhosts name | grep -qx '$(TEST_RABBITMQ_VHOST)' \
		|| docker compose exec -T rabbitmq rabbitmqctl -q add_vhost '$(TEST_RABBITMQ_VHOST)'
	@docker compose exec -T rabbitmq rabbitmqctl -q set_permissions -p '$(TEST_RABBITMQ_VHOST)' guest '.*' '.*' '.*'

migrate: infra-up ## Применить миграции к локальной базе (настройки - из .env и явных переменных)
	$(SERVICE_ENV) uv run alembic upgrade head

run-api: migrate ## API на хосте с автоперезагрузкой (порт 8000)
	$(SERVICE_ENV) uv run uvicorn app.main:app --reload

run-worker: migrate ## Воркер на хосте; адаптер модели - из .env или LLM_BACKEND=stub|ollama (для ollama - MODEL)
	$(WORKER_ENV) uv run python -m app.worker
