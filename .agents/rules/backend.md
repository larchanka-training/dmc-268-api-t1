# Правила разработки: бэкенд

Дополняет [`AGENTS.md`](../../AGENTS.md) — там критичный минимум, здесь детали. Git и задачи —
[`git-and-pr.md`](git-and-pr.md).

## Команды

```bash
uv sync --all-extras                    # установка
uv run uvicorn app.main:app --reload    # запуск, нужен DATABASE_URL
uv run ruff check .                     # стиль
uv run lint-imports                     # направление зависимостей
uv run pytest                           # тесты без базы
uv run alembic upgrade head             # применить миграции
uv run alembic heads                    # должна быть ровно одна голова
uv run python scripts/export_openapi.py # экспорт контракта API в docs/openapi/openapi.json
```

Тесты с базой: маркер `integration`, переменная `TEST_DATABASE_URL`, отдельная база — подготовка
удаляет схему.

Пропущенный тест — провал: в CI шаг pytest идёт с `PYTEST_FAIL_ON_SKIP=1`, и плагин
`tests/strict_skips.py` (подключён в `addopts`) отдаёт код 1, если в прогоне был хоть один
пропуск. Без переменной пропуск остаётся пропуском — так задумано для работы без базы. Проверить
локально, как в CI: `PYTEST_FAIL_ON_SKIP=1 uv run pytest` с `TEST_DATABASE_URL`, `TEST_RABBITMQ_URL`
и `TEST_RABBITMQ_CONTAINER`.

## Где что лежит

| Что | Где |
|---|---|
| Требования к поведению (источник правды) | `openspec/specs/*/spec.md` |
| Слои, порты, миграции, модель угроз | [`docs/BACKEND_ARCHITECTURE.md`](../../docs/BACKEND_ARCHITECTURE.md) |
| Модель данных | [`docs/erd.md`](../../docs/erd.md) |
| Контракт HTTP API | роуты и модели `app/api` (модель одной ручки — в её модуле, общая для нескольких — в `app/api/schemas.py`, ошибки — в `app/api/problems.py`); экспорт — [`docs/openapi/openapi.json`](../../docs/openapi/openapi.json) |
| Границы сервисов | `docs/SYSTEM_DESIGN.md` (готовится) |
| Принципы реализации (DRY, KISS, YAGNI) | `openspec/config.yaml`, `operations.apply.guidance` |

## Документация библиотек и проверка на практике

Стек свежее памяти моделей, поэтому API библиотек и платформ сверяем с документацией той версии,
что стоит у нас (`uv.lock`, `infra/.terraform.lock.hcl`, теги образов), а не пишем по памяти.
Источник — context7, если он подключён (как — в [`README.md`](../../README.md), «Правила разработки
и агенты»), иначе официальная документация и changelog. В запрос к context7 идут имя библиотеки и
вопрос; код проекта туда не вставляем — это внешний сервис.

Документация говорит, как задумано, а не как ведёт себя наша связка версий. Если решение держится
на поведении брокера, базы, провайдера OpenTofu или фреймворка — гонка, тайм-аут, порядок
пересоздания контейнеров, формат ответа, — проверяем его запуском до того, как писать код или
предлагать правку: интеграционный тест, отдельный контейнер того же образа, `tofu plan` на
локальном Docker. Предположение, которое не проверили, так и называем в задаче и в описании
пул-реквеста. Примеры того, что запуск опроверг: один вынос миграций из графа OpenTofu не убирает
простой API (общий `docker_image` ждёт дренажа воркера), а `create_before_destroy` у образа —
тоже (OpenTofu всё равно сначала гасит контейнер); RabbitMQ в контейнере не видит лимит
памяти cgroup; ранний отказ `RequestBodyLimitMiddleware` уходит `text/plain`, а не Problem Details.

## Слои

`app/api`, `app/application`, `app/domain`, `app/infrastructure`. Зависимости направлены внутрь.
Три контракта объявлены в `pyproject.toml`, `[tool.importlinter]`: направление слоёв, тонкость
точки входа, запрет вендоров внутри `domain` и `application`. Проверяет `uv run lint-imports`.

## Чистое ядро

Решение — функция над данными. Время, идентификаторы и случайность приходят аргументами,
поэтому тест сверяет точный timestamp, а не диапазон. Адаптер переводит данные; бизнес-правило
в адаптере означает, что оно лежит не в том слое.

## Швы и порты

Шов — место, где поведение подменяется без правки этого места. У нас швы совпадают с границами
слоёв и портами (`typing.Protocol`, адаптер порт не импортирует).

Три проверки, прежде чем заводить порт:

- **Тест на удаление.** Удалите модуль мысленно. Сложность исчезла — он проходной, удалите его.
  Всплыла в нескольких местах — окупается.
- **Один адаптер — шов гипотетический, два — настоящий.** Исключение уже в коде: у портов
  репозиториев один адаптер, и живут они ради границы слоя, а не подмены. Новый порт оправдан
  второй реализацией или границей слоя; иначе не нужен.
- **Глубина как рычаг.** Маленький интерфейс, много поведения за ним. Широкий интерфейс над
  тонкой реализацией — дефект.

Категория зависимости отвечает, нужен ли порт:

| Категория | Пример | Порт |
|---|---|---|
| Чистое вычисление | `next_status`, `deduplicate` | нет |
| Локально подменяемое | PostgreSQL через `TEST_DATABASE_URL` | есть, но ради границы слоя; тест берёт настоящую базу |
| Своё по сети | RabbitMQ (`JobQueue`) | да, минимум два адаптера |
| Чужое внешнее | Ollama (`LlmGateway`), VCS (`VcsGateway`) | да, в тестах мок |

Порт не пишется, пока его никто не вызывает: интерфейс раньше первого адаптера — догадка о
сигнатуре.

## HTTP API и контракт

Контракт `/api/v1` — код: схему OpenAPI строит FastAPI из роутов и моделей, сервер отдаёт её по
`/openapi.json` и в `/docs`. `docs/openapi/openapi.json` — экспорт этой схемы для фронтенда
(генерация типов) и ревью; руками не правится.

- **Ручка описывается в роуте целиком.** `response_model` — pydantic-модель ответа, `summary`
  и `description` по-русски, `responses` — все её ошибки моделями из `app/api/problems.py`
  через готовые словари `ERRORS_*` из `app/api/errors.py` (`{**ERRORS_404_REVIEW, **ERRORS_422}`).
  Ответ `default` со всем каталогом (`AnyProblem`) подключён ко всему приложению. Типы полей —
  настоящие: enum домена, `UUID`, а не `str`. У поля, чей смысл не очевиден из имени, —
  `Field(description=…)` (единицы, когда `null`, откуда берётся), у полей с форматом — `examples`;
  docstring модели — описание сущности в схеме. У операции — `summary`, `description` (сессия,
  `Origin`, пагинация) и `response_description`.
- **Ручка возвращает модель ответа**, а не словарь и не `JSONResponse` с данными: тело проходит
  модель и совпадает со схемой. Особый статус — в декораторе (`status_code=`); если нужны
  заголовки — `Response` с телом `model.model_dump(mode="json")`. `JSONResponse`
  собирает только `problem_response`; это проверяет `tests/api/test_contract_guards.py`.
- **Время — только `UtcDatetime`** из `app/api/schemas.py`: принимает время с зоной, отдаёт UTC
  с `Z` (`2026-10-11T10:00:00Z`). `datetime` в модели ответа — ошибка, её ловит тот же тест.
  Поля ответов и тел запросов — **snake_case**, один стиль во всём API. Тело запроса — от
  `StrictRequest` (без лишних полей, без приведения типов; enum-полю — `Field(strict=False)`);
  ответ с полями по умолчанию — от `AllFieldsResponse`, чтобы в схеме они были обязательными.
- **Статус HTTP в коде — только `HTTPStatus`**: `status_code=HTTPStatus.ACCEPTED`,
  `ApiError(HTTPStatus.NOT_FOUND, …)`, ключи `responses`, `Literal[HTTPStatus.X]` в моделях ошибок.
- **`Origin` проверяет middleware** (`app/api/origin.py`) — у `POST`/`PUT`/`PATCH`/`DELETE` под
  `/api/v1/`, кроме вебхуков, раньше разбора тела и сессии. Зависимостью роута его не проверяем;
  в `responses` такой ручки — `ERRORS_403_ORIGIN`. Пагинация — `Limit`/`Offset` из
  `app/api/pagination.py`.
- **Тип содержимого ошибок и `X-Trace-ID`** схеме дописывает `app/api/openapi.py` — в одном
  месте, у роутов их не пишем. Ответ ошибки без модели там же роняет сборку схемы.
- **Запланированная ручка** — роут-заглушка с полной схемой запроса и ответов, отвечает
  `501 NOT_IMPLEMENTED` (`raise ApiError(HTTPStatus.NOT_IMPLEMENTED, ErrorCode.NOT_IMPLEMENTED, …)`); в `description` —
  «Не реализовано: отвечает 501 NOT_IMPLEMENTED» и ссылка на задачу, если она есть.
- **После правки** ручки или модели — `uv run python scripts/export_openapi.py`, файл — в тот
  же коммит. Pre-commit хук `export-openapi` делает это сам при коммите с правкой в `app/`:
  если файл изменился, коммит останавливается — добавьте файл и повторите. CI запускает
  экспорт с `--check` и падает на устаревшем файле; то же проверяет `tests/test_docs.py`.

### Ошибки — RFC 9457 Problem Details

Любая ошибка приложения — `application/problem+json` по
[RFC 9457](https://www.rfc-editor.org/rfc/rfc9457): `type` (`urn:dmc-268:problem:<код>`),
`title` (постоянный для кода), `status`, `detail` (этот случай, без эха ввода), `instance`,
плюс расширения `code` и `trace_id`; у `VALIDATION_ERROR` — ещё `errors: [{where, reason}]`.
`instance` — путь запроса как пришёл, закодированный (`/api/v1/a%20b`); запрет на эхо ввода — про
`detail` и `errors`. `trace_id` — 32 hex (trace-id W3C), его же сервер отдаёт заголовком `X-Trace-ID`
на любом ответе и пишет в строку лога о `500`; входящим от HTTP-клиента
`X-Trace-ID`/`X-Request-ID`/`traceparent` не доверяем, `traceparent` из нашей очереди — принимаем
(«Наблюдаемость»).

- **Отказ в ручке** — `raise ApiError(статус, ErrorCode.<КОД>, detail)` из `app/api/errors.py`.
  `HTTPException` и свой `JSONResponse` с ошибкой не пишем: `HTTPException` фреймворка и
  зависимостей обработчик отдаёт общим `HTTP_ERROR` со статусом исключения, без своего кода.
- **Тело строит модель кода.** `problem_response` собирает тело через модель из
  `app/api/problems.py`, поэтому ответ и схема не расходятся: статус, которого модель кода не
  допускает, — ошибка в коде.
- **Новый код** — значение в `ErrorCode`, модель `<Что>Problem(Problem)` с константами `type`,
  `title`, `code`, `status` и примером, вариант в объединении `AnyProblem`, словарь `ERRORS_*` для
  `responses`. Пример — `.agents/templates/backend/endpoint.md`.
- **Сверку** делает `tests/test_docs.py`: каждый код — в объединении с discriminator по `code`,
  каждый ответ 4xx/5xx и `default` — `application/problem+json` с моделью ошибки, `422` — не
  `HTTPValidationError`. `tests/test_problem_details.py` прогоняет настоящие ответы сервера через
  `AnyProblem`.

## Наблюдаемость

Требования — спека `observability`, устройство — `app/infrastructure/telemetry.py`.

### Трассировка

- **Одна сущность — одно имя везде**: `review_run_id` (полный, не `run_id`) — одинаково в
  атрибутах спанов, полях логов и API.
- **Имена спанов**: этапы прогона — `review.<этап>`, проход очистки — `review.sweep`; очередь —
  по semconv messaging: `publish review_jobs` и `process review_jobs` с `messaging.system`,
  `messaging.operation.name`/`.type`, `messaging.destination.name`,
  `messaging.rabbitmq.destination.routing_key`, у обработки — `messaging.rabbitmq.message.delivery_tag`.
- **Атрибуты**: стандартные — только стабильные имена semconv (код ставит
  `OTEL_SEMCONV_STABILITY_OPT_IN=database,http`), синонимы не изобретаем; своё, чего в semconv
  нет, — в пространстве `review.*` (`review.stage`).
- **В атрибуты не кладём** промпты и ответы модели, код из диффа, токены доступа; query путей
  входа скрывает `QueryRedaction`. Текст исключения уходит только в событие `exception` — его
  чистит редакция коллектора. Описание статуса — только имя типа (`error_status`,
  `error_status_span`): описание редакцию не проходит. Свой спан, который может закрыться
  исключением, — через `error_status_span`, а не голый `start_as_current_span`. Движок
  SQLAlchemy создаётся с `hide_parameters=True`: параметры запроса не попадают в текст ошибки.
- **Сэмплирование**: `parentbased_always_on`; сэмплер по доле (`traceidratio`) не применяется —
  по каждому `X-Trace-ID` должен находиться трейс. Долю отбирает хвостовое сэмплирование в
  коллекторе.
- **OTel — только в `app/infrastructure`** и в проводке: `app/api/factory.py`,
  `app/api/trace_id.py`, `app/worker`. `app/domain` и `app/application` пишут спаны через порт
  `StageTracer`; запрет проверяет `lint-imports`.
- **Доверие контексту**: входящему HTTP `traceparent` не доверяем (глобальный пропагатор пуст),
  `traceparent` из нашей очереди принимаем — писать в неё может только API. Валидный HTTP
  `traceparent` — только span link серверного спана (`link_client_trace`, `link.source = "client"`),
  не родитель: trace id и сэмплирование остаются нашими.
- **Граница**: телеметрия — для команды. Данные продукта (шаги, токены, длительность) — из базы,
  ответы API из телеметрии не строятся.

### Логи

Структурированные логи с `trace_id` и `review_run_id` — задача #89; правила допишет она.
Уже сейчас: OTel Logs SDK в Python — статус Development, логи через него не отправляем; заменим,
когда станет Stable. `LoggingHandler` тогда — только из `opentelemetry-instrumentation-logging`:
версия в `opentelemetry.sdk._logs` устарела с 1.40.0 и будет удалена.

## Тесты

Каждая функция домена имеет тест без базы, брокера и сети. Интеграционные — только адаптеры и
миграции. Цикл и антипаттерны — скилл [`backend-tdd`](../skills/backend-tdd/SKILL.md).

## Миграции

Автосгенерированную ревизию читаем и правим перед коммитом: Alembic не видит того, что не
выражено в моделях. Голова одна, откат обязателен. Рецепты — скилл
[`alembic-migration`](../skills/alembic-migration/SKILL.md).

На стенде миграция идёт, пока работают старые API и воркер, с `lock_timeout = '5s'` и
`statement_timeout = '2min'` (`alembic/env.py`). Блокировку дольше 5 с она не ждёт — падает
и откатывается; шаг повторяет её до 3 раз, только если база осталась на исходной ревизии, потом
деплой падает; ревизию базы после сбоя summary берёт у `alembic current`. Значит: DDL по горячей таблице — короткий, перенос
данных большой таблицы — пачками, а не одним `UPDATE`. Потолок не поднимаем.

## Деплой: что сторож не пропустит

Деплой строит план OpenTofu и проверяет его до apply (`scripts/deploy_report.py`). Шаг
падает, и на сервер ничего не попадает, если план:

- **уничтожает или заменяет любой `docker_volume`** — это данные базы и брокера. Флагом не
  обходится. Сменить том — новым ресурсом, а старый снять блоком
  `removed { lifecycle { destroy = false } }`: план выйдет `forget`, том останется на сервере;
- **заменяет или удаляет контейнер `postgres` или `rabbitmq`**. Замену вызывает почти любая
  правка их блока в `infra/`: `command`, память, `log_opts`, образ (`env` план не видит —
  `ignore_changes`, там только переменные инициализации тома). Такая правка
  выкатывается только ручным запуском:
  `gh workflow run deploy.yml --ref develop -f allow_stateful_replace=true` — и это пишут в
  описании пул-реквеста. Повтор — новым запуском, а не Re-run: у повторной попытки флага нет.

Пароль базы, брокера или `replicator` меняется обычным деплоем: новый секрет
(`openssl rand -hex 24 | gh secret set <СЕКРЕТ>`; символы вне `A-Z a-z 0-9 . _ ~ -` шаг
отклоняет — они ломаются в URL подключения) и `gh workflow run deploy.yml --ref develop`, без
флага. Пароль выставляет шаг «Выставить пароли базы и брокера»; проверка и откат —
[`infra/README.md`](../../infra/README.md), «Смена паролей».

Проверить заранее, что даст правка в `infra/`: `tofu plan` и строка `must be replaced`.
Подробно — [`infra/README.md`](../../infra/README.md).
