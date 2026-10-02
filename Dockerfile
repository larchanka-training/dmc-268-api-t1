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
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
