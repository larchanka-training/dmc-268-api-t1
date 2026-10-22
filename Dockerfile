ARG PYTHON_VERSION=3.14
FROM python:${PYTHON_VERSION}-slim

COPY --from=ghcr.io/astral-sh/uv:0.12.21 /uv /usr/local/bin/uv

WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev
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
# Байткод не пишем: на стенде файловая система контейнера только для чтения,
# и попытки записать __pycache__ были бы молча проигнорированы на каждом старте.
ENV PYTHONDONTWRITEBYTECODE=1

# Не root: приложение ничего не пишет на диск, а файлы образа ему нужны только
# на чтение. Пользователь задан в образе, а не в infra/, чтобы то же действовало
# в docker compose и при ручном docker run.
RUN useradd --system --uid 10001 --no-create-home --shell /usr/sbin/nologin app
USER app

EXPOSE 8000
# --timeout-graceful-shutdown: по SIGTERM uvicorn ждёт закрытия соединений, а поток
# SSE (/reviews/{id}/events) живёт до конца прогона — десятки минут. Без предела
# через грейс-период остановки (10 с, infra/main.tf) пришёл бы SIGKILL, lifespan не
# завершился бы и спаны не выгрузились. Через 5 с uvicorn рвёт оставшиеся потоки,
# клиент переподключается. То же — в docker-compose.yml; сверяет
# tests/test_stack_descriptions.py.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--timeout-graceful-shutdown", "5"]
