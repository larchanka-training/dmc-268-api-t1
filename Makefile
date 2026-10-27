# Локальный запуск и проверка компонентов по отдельности.
# `make` или `make help` - список целей. Переменные переопределяются в команде:
#   make sandbox MODEL=qwen2.5-coder:7b CASE=04-users-sql-injection
#   make test-worker ARGS="-k loop -x"
# Рецепты без .ONESHELL: на macOS стоит GNU make 3.81.

SHELL := /bin/bash

# --- переменные ---------------------------------------------------------------

MODEL      ?= $(or $(OLLAMA_MODEL),qwen2.5-coder:1.5b)
OLLAMA_URL ?= $(or $(OLLAMA_BASE_URL),http://localhost:11434)
# Кейс корпуса tests/eval/rules/corpus/ без .yaml; пусто - все кейсы.
CASE       ?=
# Содержимое .review/rules.md: absent | rules | empty | adversarial.
RULES      ?= rules
# Сбои фейкового GitHub через пробел, см. --help песочницы: FAULTS="review-502-stored latency=0.2".
FAULTS     ?=
# Сценарий модели и кейс для sandbox-script; диапазон git для sandbox-git.
SCRIPT     ?= tests/sandbox/examples/llm-script.json
CASE_FILE  ?= tests/sandbox/examples/case.yaml
RANGE      ?= develop..HEAD
# Дополнительные ключи: для sandbox - его флаги, для test-* - флаги pytest.
ARGS       ?=

# База и брокер из docker-compose.yml (порты проброшены на 127.0.0.1).
DB_USER := dmc268
DB_PASS := dmc268
DATABASE_URL      ?= postgresql+psycopg://$(DB_USER):$(DB_PASS)@localhost:5432/dmc268
RABBITMQ_URL      ?= amqp://guest:guest@localhost:5672/%2F
# Отдельная база тестов: подготовка удаляет её схему (см. README, «Тесты»).
TEST_DATABASE_URL ?= postgresql+psycopg://$(DB_USER):$(DB_PASS)@localhost:5432/dmc268_test
TEST_RABBITMQ_URL ?= $(RABBITMQ_URL)
LLM_BACKEND       ?= stub

SANDBOX_FLAGS = --rules $(RULES) $(if $(CASE),--case $(CASE)) \
	$(foreach fault,$(FAULTS),--github-fault '$(fault)') $(ARGS)
# Окружение тестов, которым нужны PostgreSQL и RabbitMQ.
INFRA_TEST_ENV = TEST_DATABASE_URL='$(TEST_DATABASE_URL)' \
	TEST_RABBITMQ_URL='$(TEST_RABBITMQ_URL)' \
	TEST_RABBITMQ_CONTAINER="$$(docker compose ps -q rabbitmq)"
# Окружение процессов API и воркера вне Docker.
SERVICE_ENV = DATABASE_URL='$(DATABASE_URL)' RABBITMQ_URL='$(RABBITMQ_URL)' \
	GITHUB_WEBHOOK_SECRET=$${GITHUB_WEBHOOK_SECRET:-local-dev-secret}

.DEFAULT_GOAL := help
.PHONY: help install check \
	sandbox sandbox-stub sandbox-oracle sandbox-script sandbox-git ollama-up ollama-pull \
	test test-domain test-application test-worker test-api test-infrastructure \
	test-db test-queue test-e2e test-all \
	infra-up infra-down test-db-create migrate run-api run-worker stack-up stack-down

help: ## Список целей
	@grep -E '^[a-zA-Z0-9_-]+:.*## ' $(MAKEFILE_LIST) | \
		awk 'BEGIN {FS = ":.*## "}; {printf "  \033[36m%-20s\033[0m %s\n", $$1, $$2}'

install: ## Поставить зависимости (uv sync --all-extras)
	uv sync --all-extras

check: ## ruff, mypy, lint-imports
	uv run ruff check .
	uv run mypy .
	uv run lint-imports

# --- песочница воркера: всё подменено, кроме модели ------------------------------

sandbox: ollama-up ollama-pull ## Воркер на корпусе PR с локальной Ollama (MODEL, CASE, RULES, FAULTS, ARGS)
	uv run python -m tests.sandbox --llm ollama --model '$(MODEL)' --ollama-url '$(OLLAMA_URL)' $(SANDBOX_FLAGS)

sandbox-stub: ## Воркер на корпусе PR без модели, офлайн (CASE, RULES, FAULTS, ARGS)
	uv run python -m tests.sandbox --llm stub $(SANDBOX_FLAGS)

sandbox-oracle: ## Воркер на корпусе PR с оракулом: ровно размеченные нарушения (CASE, RULES, FAULTS, ARGS)
	uv run python -m tests.sandbox --llm oracle $(SANDBOX_FLAGS)

sandbox-script: ## Воркер на CASE_FILE со сценарием модели SCRIPT (RULES, FAULTS, ARGS)
	uv run python -m tests.sandbox --llm script --llm-script '$(SCRIPT)' --case-file '$(CASE_FILE)' $(SANDBOX_FLAGS)

sandbox-git: ## Воркер на диапазоне git RANGE с заглушкой модели (RULES, FAULTS, ARGS)
	uv run python -m tests.sandbox --llm stub --from-git '$(RANGE)' $(SANDBOX_FLAGS)

ollama-up: ## Поднять ollama serve в фоне, если она ещё не отвечает
	@if curl -sf '$(OLLAMA_URL)/api/tags' >/dev/null; then \
		echo "Ollama уже отвечает на $(OLLAMA_URL)"; \
	else \
		command -v ollama >/dev/null || { echo "ollama не установлена: brew install ollama" >&2; exit 1; }; \
		echo "Запускаю ollama serve в фоне, лог - .ollama.log"; \
		OLLAMA_HOST='$(OLLAMA_URL)' nohup ollama serve >.ollama.log 2>&1 & \
		for i in $$(seq 1 30); do curl -sf '$(OLLAMA_URL)/api/tags' >/dev/null && exit 0; sleep 1; done; \
		echo "Ollama не ответила за 30 с, см. .ollama.log" >&2; exit 1; \
	fi

ollama-pull: ## Скачать MODEL, если её ещё нет
	@OLLAMA_HOST='$(OLLAMA_URL)' ollama list | awk 'NR > 1 {print $$1}' | grep -qx -e '$(MODEL)' -e '$(MODEL):latest' \
		&& echo "Модель $(MODEL) уже скачана" \
		|| OLLAMA_HOST='$(OLLAMA_URL)' ollama pull '$(MODEL)'

# --- тесты по компонентам: без инфраструктуры --------------------------------------

test: ## Все чистые тесты (база и брокер не нужны)
	uv run pytest $(ARGS)

test-domain: ## Доменный слой: tests/domain
	uv run pytest tests/domain $(ARGS)

test-application: ## Use case'ы и конвейер ревью: tests/application
	uv run pytest tests/application $(ARGS)

test-worker: ## Воркер: consume-цикл, обработчик, обслуживание - tests/worker
	uv run pytest tests/worker $(ARGS)

test-api: ## Ручки API: tests/api и tests/test_*_api.py
	uv run pytest tests/api $$(ls tests/test_*_api.py) $(ARGS)

test-infrastructure: ## Адаптеры GitHub, модели, кэша, парсера: tests/infrastructure
	uv run pytest tests/infrastructure $(ARGS)

# --- тесты по компонентам: с PostgreSQL и RabbitMQ из compose ----------------------

test-db: infra-up test-db-create ## Адаптеры базы и миграции: tests/db
	$(INFRA_TEST_ENV) uv run pytest tests/db $(ARGS)

test-queue: infra-up ## Адаптер RabbitMQ: tests/queue
	$(INFRA_TEST_ENV) uv run pytest tests/queue $(ARGS)

test-e2e: infra-up test-db-create ## Сквозной стенд вебхук → воркер → GitHub: tests/e2e
	$(INFRA_TEST_ENV) uv run pytest tests/e2e $(ARGS)

test-all: infra-up test-db-create ## Весь набор как в CI: пропуск = провал
	PYTEST_FAIL_ON_SKIP=1 $(INFRA_TEST_ENV) uv run pytest $(ARGS)

# --- инфраструктура и сервисы -----------------------------------------------------

infra-up: ## Поднять PostgreSQL и RabbitMQ из compose и дождаться healthcheck
	docker compose up -d --wait postgres rabbitmq

infra-down: ## Остановить контейнеры compose (данные в томах остаются)
	docker compose down

test-db-create: ## Создать базу dmc268_test, если её нет
	@docker compose exec -T postgres psql -U $(DB_USER) -d dmc268 -tAc \
		"SELECT 1 FROM pg_database WHERE datname = 'dmc268_test'" | grep -q 1 \
		|| docker compose exec -T postgres createdb -U $(DB_USER) dmc268_test

migrate: infra-up ## Применить миграции к локальной базе
	$(SERVICE_ENV) uv run alembic upgrade head

run-api: migrate ## API на хосте с автоперезагрузкой (порт 8000)
	$(SERVICE_ENV) uv run uvicorn app.main:app --reload

run-worker: migrate ## Воркер на хосте; LLM_BACKEND=stub|ollama (для ollama - MODEL)
	$(SERVICE_ENV) LLM_BACKEND='$(LLM_BACKEND)' OLLAMA_BASE_URL='$(OLLAMA_URL)' \
		OLLAMA_MODEL='$(if $(filter ollama,$(LLM_BACKEND)),$(MODEL))' \
		uv run python -m app.worker

stack-up: ## Весь стек в Docker: база, брокер, миграция, API, воркер
	docker compose up --build

stack-down: ## Остановить весь стек
	docker compose down
