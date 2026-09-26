# DMC-268 API (Team 1)

Бэкенд на FastAPI для агента автоматического код-ревью.

Архитектура и модель данных: [`docs/BACKEND_ARCHITECTURE.md`](docs/BACKEND_ARCHITECTURE.md),
[`docs/erd.md`](docs/erd.md). Требования к поведению живут в `openspec/specs/` и меняются
только через OpenSpec-change.

## Задачи

Доска команды — GitHub Project [`dmc-268-t1`](https://github.com/orgs/larchanka-training/projects/7).
Бэкендовые тикеты заводятся в этом репозитории, фронтовые — в
[`dmc-268-ui-t1`](https://github.com/larchanka-training/dmc-268-ui-t1/issues). Доска собирает
и те, и другие.

## Правила разработки и агенты

Критичный минимум — в [`AGENTS.md`](AGENTS.md). Детали лежат в `.agents/`:

| Что | Где |
|---|---|
| Правила стека: команды, слои, швы, тесты | [`.agents/rules/backend.md`](.agents/rules/backend.md) |
| Ветки, задачи, пул-реквесты, работа с замечаниями | [`.agents/rules/git-and-pr.md`](.agents/rules/git-and-pr.md) |
| Скиллы: TDD, ревью, пул-реквест, миграции | [`.agents/skills/`](.agents/skills/) |
| Шаблоны кода и тестов | [`.agents/templates/backend/`](.agents/templates/backend/) |

Свой инструмент каждый подключает локально — каталоги инструментов не коммитятся:

```bash
ln -s ../.agents/skills .claude/skills    # или .cursor/, .codex/, .opencode/
openspec init --tools <tool>              # то же самое, если инструмент поддержан
ln -s AGENTS.md CLAUDE.md                 # Claude Code читает CLAUDE.md
```

Симлинк, а не копия: копия разойдётся с оригиналом на первой же правке. Antigravity исключение —
он читает `.agents/skills` сам.

Системные промпты ревью-агента, которого мы разрабатываем, —
[`prompts/review/README.md`](prompts/review/README.md).

## Требования

- Python 3.14, версия зафиксирована в `.python-version` и проверяется через `requires-python`.
  `uv` скачает его сам; системный `python3`, скорее всего, другой версии.
- [uv](https://docs.astral.sh/uv/) для зависимостей.
- PostgreSQL, чтобы запускать сервис и интеграционные тесты.

## Установка

```bash
uv sync --all-extras
uv run pre-commit install   # перед каждым коммитом гоняет ruff, mypy и lint-imports
```

## Запуск

### Самый короткий путь: Docker Compose

```bash
docker compose up
```

Поднимает API вместе с PostgreSQL и Redis, дожидается, пока PostgreSQL отчитается
о готовности, применяет миграции и отдаёт API на `localhost:8000`. `GET /health`
отвечает `{"status": "ok"}`. Данные PostgreSQL остаются в именованном томе и
переживают `docker compose down` / `up`; чтобы стереть их, добавьте `-v` к `down`.

Redis поднимается только как локальная инфраструктура — из API в него пока никто
не ходит. Он понадобится первой же задаче, которой нужны идемпотентность,
rate limiting или кэш (см. `docs/BACKEND_ARCHITECTURE.md`, «Добавление Redis»).

### Путь без лишних зависимостей: свой PostgreSQL

Сервис читает `DATABASE_URL` и без него не стартует. Таблицы он не создаёт:
схема появляется через миграции. Полный список переменных, которые читает
процесс, — в [`.env.example`](.env.example); скопируйте его в `.env`, если не
хотите экспортировать их руками.

```bash
docker run -d --name dmc268-db -p 5432:5432 \
  -e POSTGRES_USER=dmc -e POSTGRES_PASSWORD=dmc -e POSTGRES_DB=dmc268 \
  postgres:18-alpine

export DATABASE_URL="postgresql+psycopg://dmc:dmc@localhost:5432/dmc268"

uv run alembic upgrade head
uv run uvicorn app.main:app --reload
```

`GET /health` отвечает `{"status": "ok"}`. Сгенерированная документация API лежит на `/docs`.

Тот же стек — PostgreSQL, RabbitMQ и контейнер с API — описан ещё раз в
[`infra/`](infra/README.md), для сервера. Два описания одного стека уже дважды
разъезжались и ломали стенд, поэтому факты, которые обязаны совпадать (миграции
до приёма трафика, точка монтирования тома PostgreSQL, что публикуется наружу),
вынесены в спеку `openspec/specs/backend-delivery/`. Правя одно описание,
сверьтесь со вторым.

Если вы создавали базу на более ранней ревизии этой ветки, удалите её и создайте
заново. Базовую миграцию исправили на месте, пока она ещё не была смёржена,
поэтому старая база заявляет ревизию `0001`, но содержит ограничения в том виде,
в каком они были до исправления.

## Тесты

Всё, что ниже адаптеров, чистое, так что большей части тестов ничего не нужно.

```bash
uv run pytest                           # 79 тестов, база не нужна
TEST_DATABASE_URL=... uv run pytest     # 122 теста, вместе с адаптерами и миграциями
```

Тесты, которым нужен PostgreSQL, помечены `integration` и пропускаются, если
`TEST_DATABASE_URL` не задан.

Эта переменная намеренно не `DATABASE_URL`, и никакого отката на неё нет.
Подготовка тестовой базы удаляет её схему, так что если направить тесты на базу,
с которой работает сервис, она окажется пустой. Выделите тестам отдельную:

```bash
createdb dmc268_test
export TEST_DATABASE_URL="postgresql+psycopg://dmc:dmc@localhost:5432/dmc268_test"
```

Роли, под которой идёт подключение, нужно право создавать роли: один из тестов
создаёт роль, чтобы проверить, что пароль в percent-encoding корректно проходит
через `alembic.ini`.

## Проверки

```bash
uv run ruff check .      # стиль
uv run mypy .            # типы, строгий режим на app/
uv run lint-imports      # правило слоёв, см. документ по архитектуре
```

`lint-imports` роняет сборку, когда модуль импортирует что-то из внешнего слоя,
например когда что-нибудь в `app/domain` тянется к SQLAlchemy. `mypy` работает в
строгом режиме по `app/`; для `tests/` действует послабление
(`[[tool.mypy.overrides]]` в `pyproject.toml`), потому что стиль pytest —
нетипизированные фикстуры и `def test_x():` — там норма. Все три проверки
работают и как хуки `pre-commit` (`uv run pre-commit install`, см. «Установка»),
и в CI.

## Миграции

```bash
uv run alembic upgrade head        # применить
uv run alembic downgrade base      # откатить, ничего после себя не оставив
uv run alembic heads               # должна быть ровно одна
```

Сгенерированные миграции проходят ревью перед коммитом. Соглашения и рецепт с
`ALTER TYPE` для расширения enum описаны в документе по архитектуре.
