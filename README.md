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

Актуальную документацию библиотек агент берёт из [context7](https://context7.com) — MCP-сервера,
который отдаёт документацию нужной версии. Подключение тоже личное, конфиг MCP не коммитится:

```bash
claude mcp add --scope user --transport http context7 https://mcp.context7.com/mcp   # Claude Code
npx ctx7 setup --cursor                    # или --claude, --opencode
```

Для остальных инструментов — [инструкция context7](https://context7.com/docs/resources/all-clients).
Ключ API необязателен, с ним выше лимиты. Правило, когда сверяться с документацией, — в
[`.agents/rules/backend.md`](.agents/rules/backend.md), «Документация библиотек и проверка на
практике».

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
uv run pre-commit install   # перед каждым коммитом гоняет ruff, mypy, lint-imports и openspec validate (нужен Node)
```

## Запуск

### Самый короткий путь: Docker Compose

```bash
docker compose up
```

Поднимает API и воркер вместе с PostgreSQL и RabbitMQ. Сначала PostgreSQL
отчитывается о готовности, затем одноразовый сервис `migrate` применяет миграции
(`alembic upgrade head`) и завершается с кодом 0; API и воркер стартуют только
после этого, поэтому миграции идут один раз, а не при каждом старте API. Порты
(`8000`, `5432`, `5672`, `6379`) опубликованы только на `127.0.0.1`: с других
машин стек недоступен. API отдаётся на `localhost:8000`, `GET /health` отвечает
`{"status": "ok"}`. Данные PostgreSQL остаются в именованном томе и переживают
`docker compose down` / `up`; чтобы стереть их, добавьте `-v` к `down`.

Воркеру нужен `LLM_BACKEND` (см. раздел о модели ниже): без него он не стартует.

Redis — локальная инфраструктура без потребителей: из API и воркера в него никто
не ходит, поэтому по умолчанию он не поднимается. Нужен — включите профиль:
`docker compose --profile redis up -d redis`. Он понадобится первой же задаче,
которой нужны идемпотентность, rate limiting или кэш (см.
`docs/BACKEND_ARCHITECTURE.md`, «Добавление Redis»).

### Путь без лишних зависимостей: свой PostgreSQL

Сервис читает `DATABASE_URL` и без него не стартует. Таблицы он не создаёт:
схема появляется через миграции. Полный список переменных, которые читает
процесс, — в [`.env.example`](.env.example); скопируйте его в `.env`, если не
хотите экспортировать их руками. Секрет вебхуков там — `local-dev-secret`, тот же,
что в `docker-compose.yml`: заглушка для локального запуска, с настоящей GitHub App
его заменяют на webhook secret из её настроек.

```bash
docker run -d --name dmc268-db -p 5432:5432 \
  -e POSTGRES_USER=dmc -e POSTGRES_PASSWORD=dmc -e POSTGRES_DB=dmc268 \
  postgres:18-alpine

export DATABASE_URL="postgresql+psycopg://dmc:dmc@localhost:5432/dmc268"

uv run alembic upgrade head
uv run uvicorn app.main:app --reload
```

`GET /health` отвечает `{"status": "ok"}`. Контракт API строится из кода: сервер отдаёт схему по
`/openapi.json` и в Swagger UI на `/docs`, её экспорт лежит в `docs/openapi/openapi.json` — по нему
фронтенд генерирует типы. После правки ручки или модели ответа обновите экспорт, CI проверяет, что
он свежий:

```bash
uv run python scripts/export_openapi.py           # записать docs/openapi/openapi.json
uv run python scripts/export_openapi.py --check   # то же, что в CI: rc=1, если файл устарел
```

Ручки `/api/v1`, кроме вебхуков и входа, требуют сессию. Для входа через GitHub
нужны `GITHUB_APP_CLIENT_ID`, `GITHUB_APP_CLIENT_SECRET` и `PUBLIC_ORIGIN`
(см. `.env.example`), а callback `<PUBLIC_ORIGIN>/api/v1/auth/github/callback`
должен быть зарегистрирован в настройках GitHub App. `PUBLIC_ORIGIN` — origin, на
котором браузер открывает и кабинет, и API (на стенде они за одним nginx). Локально
`http://localhost:8000` из `.env.example` подходит, когда API открывают напрямую.
Для кабинета с настоящим бэкендом (`VITE_USE_MOCK_API=false`, dev-сервер на `:3000`
проксирует `/api` сюда) ставьте `http://localhost:3000`.
Cookie сессии ставится с `Secure`: вход работает по HTTPS и на `localhost`, по
голому HTTP на другом хосте — нет.

### Ключ шифрования токенов пользователя

При входе сервис сохраняет токен пользователя GitHub App (user access token, а если у
App включено истечение токенов — и refresh token) при сессии: по нему GitHub отдаёт
список репозиториев, доступных пользователю. В базе токен лежит только зашифрованным
(AES-256-GCM, таблица `provider_tokens`), ключ — настройка `USER_TOKEN_ENCRYPTION_KEY`:
32 случайных байта в base64.

```bash
openssl rand -base64 32        # ключ для .env или для секрета стенда
# стенд: секрет, который deploy.yml передаёт в TF_VAR_user_token_encryption_key
openssl rand -base64 32 | gh secret set USER_TOKEN_KEY_DMC268_T1 -R larchanka-training/dmc-268-api-t1
```

Ключ не задан — вход отвечает `/login?error=provider_unavailable` (токен открытым не
пишется никогда), ключ битый — API не стартует и называет настройку, не печатая
значение. На стенде без секрета `USER_TOKEN_KEY_DMC268_T1` падает уже `tofu apply`.
Смена ключа делает сохранённые токены нечитаемыми: ручки репозиториев отвечают `401`,
сессия закрывается, пользователь входит заново. Сессии, открытые до хранения токенов,
ведут себя так же.

Тот же стек — PostgreSQL, RabbitMQ и контейнеры API и воркера — описан ещё раз в
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

Короткие команды - в `Makefile`, список - `make help`: `make check` (статические проверки как в CI),
`make test-db` (сам поднимет PostgreSQL и RabbitMQ из compose и базу `dmc268_test`), `make test-all`
(весь набор как в CI), `make sandbox-demo` (песочница воркера с проверкой окружения), `make run-worker` и т. д.
Флаги pytest или песочницы передаются через `ARGS`: `make test-db ARGS="-k migration -x"`.
Тесты с брокером идут в отдельный vhost `dmc268_test` (`make test-queue-vhost`), а не в `/` стека разработки.
`make run-api`, `make run-worker` и `make migrate` берут настройки из `.env`: процессу make передаёт только
то, что вы задали явно - в команде (`make run-worker LLM_BACKEND=ollama`) или в окружении оболочки.

```bash
uv run pytest                                       # чистые тесты, база не нужна
TEST_DATABASE_URL=... TEST_RABBITMQ_URL=... uv run pytest   # вместе с адаптерами, брокером и E2E
```

Маркеры `live`, `eval` и `load` исключены в `addopts` (`pyproject.toml`), поэтому
ключ `-m` в обычном прогоне не нужен: явный `-m` перекрывает это исключение.

Тесты, которым нужен PostgreSQL, помечены `integration` и пропускаются, если
`TEST_DATABASE_URL` не задан; сквозным (`tests/e2e/`), обвязке eval и малому
нагрузочному прогону нужен ещё и RabbitMQ в `TEST_RABBITMQ_URL`.
Тест алярма брокера (`tests/queue/test_broker_blocked.py`) включает алярм памяти
через `docker exec` в контейнере RabbitMQ: имя или идентификатор контейнера —
в `TEST_RABBITMQ_CONTAINER`, без него тест пропускается.

В CI пропуск — провал: шаг pytest идёт с `PYTEST_FAIL_ON_SKIP=1`, и плагин
`tests/strict_skips.py` (подключён в `addopts`) завершает прогон с кодом 1, если хоть один тест
пропущен, и перечисляет пропущенные. Локально так же — той же переменной:

```bash
PYTEST_FAIL_ON_SKIP=1 TEST_DATABASE_URL=... TEST_RABBITMQ_URL=... TEST_RABBITMQ_CONTAINER=... uv run pytest
```

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

Живые тесты (`tests/live/`, маркер `live`) ходят в настоящий EUrouter и платные, поэтому основной прогон CI их исключает.
Их запускает workflow `LLM live` (`.github/workflows/llm-live.yml`) с ключом организации - секрет `AI_DMC268_T1` и переменная `AI_DMC268_URL` - на pull request и push, меняющих код модели или промпты, и вручную (`gh workflow run "LLM live"`).
Запросы в них синтетические: код проверяемых репозиториев наружу не уходит.
Без ключа тесты пропускаются; локально с ключом:

```bash
EUROROUTER_API_KEYS=... EUROROUTER_BASE_URL=https://api.eurouter.ai/api/v1 \
EUROROUTER_MODEL=qwen3-coder-next-80b uv run pytest -m live tests/live
```

Сквозной стенд (`tests/e2e/stack.py`) прогоняет подписанный вебхук через API, базу, RabbitMQ и воркер до публикации в GitHub; GitHub подменён на уровне HTTP, модель - за портом.
На нём же стоят eval правил и нагрузочный прогон; сценарии и результаты - в [`docs/testing/RULES_E2E_REPORT.md`](docs/testing/RULES_E2E_REPORT.md).

- Eval правил на живой модели (маркер `eval`): `gh workflow run "LLM eval"` или локально с ключами EUrouter и сервисами `uv run pytest -s -m eval tests/eval`; отчёт - в `EVAL_REPORT_DIR`.
- Полный нагрузочный прогон (маркер `load`): `gh workflow run Load` или локально `uv run pytest -s -m load tests/load`; профиль и пороги - переменными `LOAD_*` (см. `tests/load/test_load.py`).

### Локальный прогон воркера без инфраструктуры

Настоящий воркер и пайплайн ревью на мок-PR, без Docker, PostgreSQL, RabbitMQ и GitHub — нужна только
локальная Ollama. Одна команда проверит окружение, поднимет Ollama, скачает модель и прогонит мок-кейс:

```bash
make sandbox-demo
```

Свои мок-данные, режимы без модели (`make sandbox-stub`, `sandbox-oracle`, `sandbox-script`), сбои
очереди, все ключи и коды выхода — [`tests/sandbox/README.md`](tests/sandbox/README.md).

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
нетипизированные фикстуры и `def test_x():` — там норма. Все три проверки, как и `openspec validate --strict`,
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

## Регистрация репозитория

Вебхуки репозиториев, которых нет в системе, сервис игнорирует: регистрацию
по вебхуку спека запрещает — её выполняет отдельная операция с авторизацией.

Основной путь — кабинет: экран «Подключить репозиторий» показывает репозитории
установок GitHub App (`GET /api/v1/repositories/available`) и подключает выбранный
(`POST /api/v1/repositories`). Список отдаёт сам GitHub по токену пользователя,
сохранённому при входе (`GET /user/installations` и
`GET /user/installations/{id}/repositories`): это ровно те репозитории установок, к
которым у пользователя есть доступ, с его правами на каждый. Подключение — два запроса:
репозиторий по id токеном пользователя и установка приложения на нём (JWT приложения).
Подключить можно только с правом admin или maintain; роль read на приватном репозитории —
`403`. Репозиторий, которого пользователь не видит, публичный только с правом чтения (так
GitHub отдаёт любой публичный репозиторий) и репозиторий, на
котором приложение не установлено или установка приостановлена, — один и тот же `404`. Приложение должно быть установлено на репозиторий,
иначе его нет в списке. Запросы идут из лимита пользователя, а не установки.
`installation_id` не хранится. Нужен ключ `USER_TOKEN_ENCRYPTION_KEY` (см. «Ключ
шифрования токенов пользователя»); отдельных разрешений App сверх **Metadata: read**
список не требует. Если у App включено истечение user access token, сервис сам обновляет
токен по refresh token.

Для разработки, без кабинета и установленного App, репозиторий можно внести вручную:

```bash
psql "$DATABASE_URL" -c "
  INSERT INTO repositories (id, provider, provider_id, full_name,
                            default_branch, auto_review_enabled)
  VALUES (gen_random_uuid(), 'github', '<числовой id репозитория>',
          '<owner>/<имя>', '<ветка по умолчанию>', true);"
```

Числовой `id` и `full_name` репозитория — из API хостинга (`GET /repos/{owner}/{repo}`).
Повторная вставка того же `(provider, provider_id)` упадёт на уникальном
индексе `uq_repositories_provider_id` — повторное внесение невозможно по
конструкции.

## Правила ревью и разрешения GitHub App

Воркер довыбирает через contents API `.review/rules.md` (с коммита целевой
ветки PR) и изменённые файлы (с `head_sha`) для AST-окружения, поэтому GitHub
App нужно разрешение **Contents: read**. Без него прогон не падает: правила
уходят в дефолтный фоллбэк (`rules.status = unavailable`), AST пропускается.
Формат правил — [`prompts/review/custom-rules.md`](prompts/review/custom-rules.md),
лимиты сборки контекста (`CONTEXT_MAX_TOKENS`, `DIFF_MAX_BYTES`, `RULES_MAX_BYTES`,
`AST_MAX_FILES`, …) — в [`app/config.py`](app/config.py).

## Merge gate

После публикации ревью бот ставит на `head_sha` check run `dmc-268-ai-review`: `failure`,
пока открыт тред с замечанием `critical`, иначе `success`. Когда тред закрывают, бот
пересчитывает проверку сразу, нового пуша не нужно. Для этого App нужно разрешение
**Checks: write** и подписка на событие `pull_request_review_thread`. Мердж блокирует только обязательная проверка, и делать её
обязательной надо **с источником — нашим App**. Проверку «по имени» закрывает любой check run
с тем же именем, например из workflow автора PR с `checks: write`, и любой commit status от
того, у кого есть право записи. С источником GitHub не даст смёржить, если статус выставил
кто-то другой.

В интерфейсе: Settings → Rules → Rulesets → New branch ruleset → Require status checks
to pass → Add checks → `dmc-268-ai-review`, источник — наш App вместо «Any source». App
появляется в списке источников, только когда он недавно ставил check run в этом
репозитории: сначала прогоните ревью на любом PR. Через API источник задаёт
`integration_id` — id App (переменная репозитория `APP_ID_DMC268_T1`, у нас `5262828`):

```bash
gh api -X POST repos/<owner>/<repo>/rulesets --input - <<'EOF'
{
  "name": "Merge gate ревью-бота",
  "target": "branch",
  "enforcement": "active",
  "conditions": {"ref_name": {"include": ["~DEFAULT_BRANCH"], "exclude": []}},
  "rules": [{
    "type": "required_status_checks",
    "parameters": {
      "strict_required_status_checks_policy": false,
      "required_status_checks": [
        {"context": "dmc-268-ai-review", "integration_id": 5262828}
      ]
    }
  }]
}
EOF
```

Обязательную проверку проходят `success`, `neutral` и `skipped`, блокируют `failure` и
`action_required`. Наш бот ставит только `success` и `failure`.

Проверено на практике (2026-10-24, временный ruleset в этом репозитории): `success` с тем же
именем от пользователя проверку с источником не закрывает, `success` от нужного App —
закрывает. Но `failure` с тем же именем от любого, у кого есть право записи, мердж
блокирует: обойти gate так нельзя, придержать — можно.

## Модель

Воркер обращается к модели через адаптер, который выбирает `LLM_BACKEND`; без этой переменной воркер не стартует.

- `stub` - заглушка без похода к модели: прогон доходит до `completed` с пустым ревью. Её выбирает `.env.example`; у `docker compose` значения по умолчанию нет.
- `ollama` - локальная модель в нашей сети (`OLLAMA_BASE_URL`, `OLLAMA_MODEL`).

Локальная модель поднимается профилем compose:

```bash
docker compose --profile ollama up -d
docker compose exec ollama ollama pull qwen2.5-coder:7b
# в .env: LLM_BACKEND=ollama, OLLAMA_MODEL=qwen2.5-coder:7b
docker compose up -d worker
```

Eurorouter (OpenAI-совместимый hosted-провайдер) подключается только фоллбэком после локальной модели и только при `LLM_HOSTED_FALLBACK=true` вместе с `EUROROUTER_BASE_URL`, `EUROROUTER_MODEL` и `EUROROUTER_API_KEYS` (ключи через запятую).
Включение отправляет код проверяемых репозиториев за пределы нашей сети; какие провайдеры включены, воркер пишет в лог при старте.

Окно модели (`LLM_CONTEXT_WINDOW_TOKENS`) должно вмещать пакет контекста (`CONTEXT_MAX_TOKENS`), системный промпт и резерв ответа (`LLM_MAX_OUTPUT_TOKENS`): иначе воркер не стартует.
Все переменные с пояснениями - в [`.env.example`](.env.example).

## Трассировка

API и воркер пишут спаны OpenTelemetry: HTTP-запрос, вызовы GitHub и модели,
запросы к базе, публикация в очередь и обработка в воркере по этапам — один
трейс на прогон; его trace id клиент видит в `X-Trace-ID`. По умолчанию спаны
никуда не уходят. Посмотреть их локально:

```bash
OTEL_TRACES_EXPORTER=console uv run uvicorn app.main:app --reload
OTEL_TRACES_EXPORTER=console uv run python -m app.worker
```

`OTEL_SERVICE_NAME` переопределяет имя сервиса (`dmc-268-api`, `dmc-268-worker`),
`OTEL_SDK_DISABLED=true` выключает трассировку. Атрибуты спанов SQL и httpx — стабильные
имена semconv (`db.query.text`, `http.request.method`, `url.full`): без
`OTEL_SEMCONV_STABILITY_OPT_IN` код ставит `database,http` сам. Экспорт в хранилище
трейсов — #89.
Это телеметрия для команды: данные прогона для кабинета — длительность, токены,
шаги — берутся из базы.
