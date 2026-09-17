# DMC-268 API (Team 1)

Бэкенд на FastAPI для агента автоматического код-ревью.

Архитектура и модель данных: [`docs/BACKEND_ARCHITECTURE.md`](docs/BACKEND_ARCHITECTURE.md),
[`docs/erd.md`](docs/erd.md).

## Требования

- Python 3.14, версия зафиксирована в `.python-version` и проверяется через `requires-python`.
  `uv` скачает его сам; системный `python3`, скорее всего, другой версии.
- [uv](https://docs.astral.sh/uv/) для зависимостей.
- PostgreSQL, чтобы запускать сервис и интеграционные тесты.

## Установка

```bash
uv sync --all-extras
```

## Запуск

Сервис читает `DATABASE_URL` и без него не стартует. Таблицы он не создаёт:
схема появляется через миграции.

```bash
docker run -d --name dmc268-db -p 5432:5432 \
  -e POSTGRES_USER=dmc -e POSTGRES_PASSWORD=dmc -e POSTGRES_DB=dmc268 \
  postgres:18-alpine

export DATABASE_URL="postgresql+psycopg://dmc:dmc@localhost:5432/dmc268"

uv run alembic upgrade head
uv run uvicorn app.main:app --reload
```

`GET /health` отвечает `{"status": "ok"}`. Сгенерированная документация API лежит на `/docs`.

Весь локальный стек (PostgreSQL, RabbitMQ и контейнер с API) также описан на
Terraform в [`infra/`](infra/README.md); это результат DevOps-тикета. Команды
выше дают путь без лишних зависимостей.

Если вы создавали базу на более ранней ревизии этой ветки, удалите её и
создайте заново. Базовую миграцию исправили на месте, пока она ещё не была
смёржена, поэтому старая база заявляет ревизию `0001`, но содержит ограничения
в том виде, в каком они были до исправления.

## Тесты

Всё, что ниже адаптеров, чистое, так что большей части тестов ничего не нужно.

```bash
uv run pytest                           # 78 tests, no database required
TEST_DATABASE_URL=... uv run pytest     # 121 tests, adapters and migrations included
```

Тесты, которым нужен PostgreSQL, помечены `integration` и пропускаются, если
`TEST_DATABASE_URL` не задан.

Эта переменная намеренно не `DATABASE_URL`, и никакого отката на неё нет.
Подготовка тестовой базы удаляет её схему, так что если направить тесты на
базу, с которой работает сервис, она окажется пустой. Выделите тестам отдельную:

```bash
createdb dmc268_test
export TEST_DATABASE_URL="postgresql+psycopg://dmc:dmc@localhost:5432/dmc268_test"
```

Роли, под которой идёт подключение, нужно право создавать роли: один из тестов
создаёт роль, чтобы проверить, что пароль в percent-encoding корректно
проходит через `alembic.ini`.

## Проверки

```bash
uv run ruff check .      # style
uv run lint-imports      # the layering rule, see the architecture document
```

`lint-imports` роняет сборку, когда модуль импортирует что-то из внешнего слоя,
например когда что-нибудь в `app/domain` тянется к SQLAlchemy.

## Миграции

```bash
uv run alembic upgrade head        # apply
uv run alembic downgrade base      # reverse, leaves nothing behind
uv run alembic heads               # must report exactly one
```

Сгенерированные миграции проходят ревью перед коммитом. Соглашения и рецепт с
`ALTER TYPE` для расширения enum описаны в документе по архитектуре.
