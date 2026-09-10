# DMC-268 API (команда 1)

Backend на FastAPI для DMC-268, команда 1. Зависимости только в `pyproject.toml` и `uv.lock`.

## Запуск

```bash
uv sync
uv run uvicorn main:app --reload
```

## Проверки качества

```bash
uv sync --all-extras
uv run python -m compileall -q .
uv run ruff check .
uv run lint-imports
uv run pytest
```

API работает на **Python 3.14** (образ `python:3.14-slim`). PostgreSQL, RabbitMQ и контейнер API поднимаются Terraform из каталога `infra/`.
