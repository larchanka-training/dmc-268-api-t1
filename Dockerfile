ARG PYTHON_VERSION=3.14

# Сборка: uv и его кэш остаются в этом этапе и в рантайм-образ не попадают.
# Схема — из официального гайда uv для Docker (multi-stage с копией .venv).
FROM python:${PYTHON_VERSION}-slim AS builder
COPY --from=ghcr.io/astral-sh/uv:0.13.5 /uv /usr/local/bin/uv

# Байткод зависимостей компилируется при сборке, а не при каждом старте: на
# стенде файловая система контейнера только для чтения, и без этого импорт
# каждый раз шёл бы по исходникам. UV_PYTHON_DOWNLOADS=0 — берём интерпретатор
# образа: тот же путь есть в рантайм-этапе, и venv в нём не ломается.
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=0

WORKDIR /app
# Проект не пакет (нет build-system): uv ставит только зависимости, без extras
# dev. Слой зависит лишь от pyproject.toml и uv.lock — правка кода его не сбрасывает.
RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    uv sync --frozen --no-dev

# Рантайм: тот же базовый образ, без uv.
FROM python:${PYTHON_VERSION}-slim

WORKDIR /app
COPY --from=builder /app/.venv /app/.venv
COPY pyproject.toml uv.lock ./
COPY app ./app
# alembic.ini and the migration scripts: without them the image can start the
# API but cannot apply its own schema, which the compose stack needs to do
# on startup.
COPY alembic.ini ./
COPY alembic ./alembic
# Тексты промптов: воркер читает их при старте и без них не поднимается
# (app/infrastructure/prompts.py ищет их рядом с пакетом app).
COPY prompts ./prompts

ENV PATH="/app/.venv/bin:$PATH"
# Байткод кода приложения компилируем при сборке; при старте не пишем: на стенде
# файловая система контейнера только для чтения, и попытки записать __pycache__
# были бы молча проигнорированы на каждом старте.
ENV PYTHONDONTWRITEBYTECODE=1

# Один слой: байткод приложения; удаление pip; пользователь.
# pip из базового образа приложению не нужен (зависимости ставит uv на этапе
# сборки), а trivy находит HIGH в vendored-копиях msgpack, urllib3 и setuptools
# внутри pip: убрать пакет надёжнее, чем глушить находки. Системный python, а не
# venv: pip стоит в нём.
# Не root: приложение ничего не пишет на диск, а файлы образа ему нужны только
# на чтение. Пользователь задан в образе, а не в infra/, чтобы то же действовало
# в docker compose и при ручном docker run.
RUN python -m compileall -q app alembic \
    && /usr/local/bin/python -m pip uninstall -y pip \
    && useradd --system --uid 10001 --no-create-home --shell /usr/sbin/nologin app
USER 10001

EXPOSE 8000
# --timeout-graceful-shutdown: по SIGTERM uvicorn ждёт закрытия соединений, а поток
# SSE (/api/v1/events) живёт, пока открыта вкладка кабинета, — часами. Без предела
# через грейс-период остановки (10 с, infra/app.tf) пришёл бы SIGKILL, lifespan не
# завершился бы и спаны не выгрузились. Через 5 с uvicorn рвёт оставшиеся потоки,
# клиент переподключается. То же — в docker-compose.yml; сверяет
# tests/test_stack_descriptions.py.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--timeout-graceful-shutdown", "5"]
