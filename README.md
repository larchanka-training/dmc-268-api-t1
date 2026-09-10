# DMC-268 API (команда 1)

Backend на FastAPI для DMC-268, команда 1.

## Запуск

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn main:app --reload
```

## Проверки качества

```bash
pip install -r requirements-dev.txt
python -m compileall -q .
ruff check .
pytest
```

API работает на **Python 3.14** (образ `python:3.14-slim`). PostgreSQL, RabbitMQ и контейнер API поднимаются Terraform из каталога `infra/`.
