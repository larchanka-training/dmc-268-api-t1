# Песочница tree-sitter

Офлайн-прогон этапа сборки контекста: настоящие `fetch_review_diff` → `build_review_context` на
мок-PR, а отчёт по каждому файлу — ровно то, что этап ответил: элементы блока `ast` пакета для
модели, записи `omitted` и охват (`coverage`). Своих правил выбора у песочницы нет, поэтому она не
расходится с приложением. Без Ollama, Docker, PostgreSQL, RabbitMQ и сети. Песочница воркера
целиком — [`../README.md`](../README.md).

## Быстрый старт

Из корня репозитория:

```bash
make sandbox-outline-demo
```

| Шаг | Что происходит | Если не так |
|---|---|---|
| 1. Зависимости | `uv sync --all-extras`: Python-пакеты, `tree-sitter` и четыре грамматики | ошибка `uv` — в выводе; `tree-sitter` 0.26.0 исключён в `pyproject.toml` (#226) |
| 2. Разбор | мок-PR [`examples/outline-case.yaml`](examples/outline-case.yaml): Python, TypeScript, TSX, JavaScript, Go, Markdown | итог и подсказка — последними строками; код 139 — segfault, см. «Что пошло не так» |

Повторный запуск — меньше секунды.

### Как читать отчёт

Выдержка из `make sandbox-outline-demo`:

```text
песочница tree-sitter: PR 1, правила: rules, лимит пакета: 32000 токенов
=== [1] outline-case: Мультиязычный мок-PR
app/billing/discount.py  python  изменены строки 13  охват: reviewed
  AST не выбирался
...
docs/orders.md  изменены строки 1-3  охват: reviewed
  AST не выбирался
app/orders/service.py  python  изменены строки 120-121  охват: reviewed
  ast/scopes (как видит модель):
    <<<untrusted kind=scopes id=3cc1471d1a3243f3ba839629c5bf1ed2>>>
    47-186 class OrderService
      117-123 def pay(self, order: Order, amount: Decimal, now: datetime) -> None
    <<<end id=3cc1471d1a3243f3ba839629c5bf1ed2>>>
  ast/imports (как видит модель):
    <<<untrusted kind=imports id=822b1241468fad1363b714ce374a74d4>>>
    from dataclasses import dataclass, field
    ...
    <<<end id=822b1241468fad1363b714ce374a74d4>>>
пакет: файлов с AST: 1, ~156 токенов; весь пакет ~2297 из 32000; omitted: 0 из 0; уровни: diff, surrounding, whole_file, ast
проблемы: -
итого: PR 1, файлов с AST 1, проблем 0, время 0.1 с
```

- **строка файла** — путь, язык (у файла без грамматики его нет), строки новой версии, которые
  затронул дифф, и группа охвата из `coverage` пакета: `reviewed`, `partial`, `not_reviewed`,
  `filtered`; `-` — файла в охвате нет (нет hunk'ов);
- **`ast/scopes`, `ast/imports`** — элементы блока `ast` пакета этого файла, текст целиком, как его
  видит модель, вместе с обёрткой недоверенных данных: охватывающие правку определения
  (`начало-конец сигнатура`, вложенные — с отступом) и импорты файла;
- **`omitted: …`** — записи `omitted` пакета этого файла: `scopes - budget`, `ast - file_limit`,
  `hunk:7 - budget`;
- **`AST не выбирался`** — у файла нет ни элементов `ast`, ни записей о них в `omitted`: файл без
  грамматики, удалён, без hunk'ов, ни один hunk файла не вошёл в пакет (охват `not_reviewed`),
  правка вне определений, или файл вошёл в дифф пакета целиком.
  Какой случай — этап не сообщает, песочница не угадывает. Здесь маленькие файлы мок-PR (до 8 КБ)
  вошли целиком — это видно по `whole_file` в «уровни»; AST есть только у `app/orders/service.py`;
- **`AST в пакете нет; причина могла не войти в урезанный omitted`** — то же, но перечень `omitted`
  урезан бюджетом, и запись о файле могла не войти;
- **`omitted вне файлов диффа`** — записи без файла диффа: правила, заголовок PR, коммиты;
- **пакет** — сколько файлов дали AST и его токены, размер всего пакета против лимита, сколько
  записей `omitted` вошло из общего числа и уровни пакета (`payload.tiers`); при урезанном
  перечне ниже — строка `перечень omitted урезан бюджетом`.

С `ARGS=--full-outline` у файла с грамматикой ещё и **весь контур** — отдельный разбор tree-sitter
того же текста: число определений и импортов и их строки. Это не часть пакета, а то, из чего этап
выбирал.

## Что настоящее, а что подменено

| Компонент | В песочнице |
|---|---|
| `fetch_review_diff`, `build_review_context`, бюджет пакета, выбор AST | настоящие |
| tree-sitter и грамматики (`TreeSitterDefinitionExtractor`) | настоящие |
| Лимиты контекста | из `Settings`, как у воркера (`context_limits`) |
| GitHub | `FakeGitHub` за настоящими `GitHubAppAuth` и `GitHubVcsGateway`; дифф — `difflib`, а с `LINES`/`lines:` — сразу по номерам строк (`sources.touched_diff`): затронутые строки добавлены (`+`), вокруг — 3 строки контекста, база — head без этих строк |
| База, кэш правил | `FakeUnitOfWork`, `FakeCache` в памяти |
| Модель | не вызывается |

## Свои данные

```bash
make sandbox-outline FILE=app/infrastructure/parsing/tree_sitter_extractor.py LINES=72-95
make sandbox-outline RANGE=develop..HEAD            # свои коммиты
make sandbox-outline CASE=04-users-sql-injection    # кейс корпуса eval
make sandbox-outline CASE_FILE=путь/кейс.yaml       # свой YAML-кейс
```

- **`FILE` + `LINES`** — один файл; `LINES` — `N`, `N-M` или список через запятую (`3-7, 12`).
  Без `LINES` файл новый и затронут целиком: файл до 8 КБ входит в дифф пакета целиком
  (`whole_file`) и AST не получает, у файла больше — в `ast` все определения, если влезут в
  бюджет. `FILE` и `LINES` передаются только аргументами make
  (`make sandbox-outline FILE=… LINES=…`), окружение игнорируется: `LINES` выставляет терминал.
- **`RANGE`** — диапазон `BASE..HEAD`, дифф — от merge-base, как у PR; файлы больше 200 000 байт
  пропускаются.
- **`CASE_FILE`** — YAML в формате корпуса (`title`, `pull_request`, `files`) плюс необязательный
  ключ `lines:` — какие строки файла изменены:

  ```yaml
  lines:
    app/billing/discount.py: "13"
    web/api/handler.ts: "4, 11"
  ```

  Строки из `lines:` считаются добавленными: база файла — тот же файл без них. Без `lines:`
  все файлы кейса новые. Тот же кейс годится и для `make sandbox CASE_FILE=…` — там ключ
  игнорируется.

Пример выше — иллюстрация: правка файла сдвинет номера строк.

## Все параметры

Флаги — через `ARGS`: `make sandbox-outline ARGS="--full-outline --context-max-tokens 2048"`.

| Ключ | Что делает |
|---|---|
| `--case ID`, `--case-file PATH`, `--from-git RANGE` (+ `--repo DIR`), `--file PATH` | входы, повторяемые; без них — мок-PR |
| `--lines SPEC` | изменённые строки — только с одним `--file` |
| `--rules {absent,rules,empty,adversarial}` | `.review/rules.md` целевой ветки; занимает часть бюджета, как у воркера (по умолчанию `rules`) |
| `--context-max-tokens N` | потолок пакета; минимум 1024 |
| `--full-outline` | ещё и весь контур каждого файла с грамматикой; упавший разбор — строка `весь контур: разбор упал: <тип>` |
| `--json PATH` | весь результат списком PR в файл |
| `-v` | логи уровня INFO |

Остальные лимиты (`DIFF_MAX_BYTES`, `AST_MAX_FILES`, `AST_MAX_FILE_BYTES`, …) берутся из окружения
процесса: `DIFF_MAX_BYTES=100 make sandbox-outline`. `.env` не читается.

Коды выхода: `0` — всё разобрано; `1` — у PR не получен дифф или в видимом `omitted` есть
`parse_error`, `missing`, `unavailable`; `2` — вход или настройки не подходят (нет файла, плохой
`--lines`, битый YAML, `--context-max-tokens` меньше 1024) или не записан `--json`; `139` —
segfault процесса, не наш код.

## Что пошло не так

| Симптом | Что сделать |
|---|---|
| код 139, segfault | `tree-sitter` 0.26.0 (#226) — `uv sync --all-extras` поставит версию из `pyproject.toml` |
| `parse_error` | экстрактор упал на файле — код 1; файл и причина в «проблемы». Если перечень `omitted` урезан бюджетом и запись не вошла, песочница её не видит — как и модель; `--context-max-tokens` больше |
| синтаксическая ошибка в файле | не ошибка песочницы: tree-sitter разбирает частично, `parse_error` нет. Определение может пропасть (незакрытая скобка) или сигнатура захватит кусок тела (висящий оператор) |
| `предупреждение: пустой контур` (`--full-outline`) | в непустом файле нет ни определений, ни импортов: нормально для `__init__.py` из docstring или модуля констант; на файле с `def`/`class` — сломан запрос грамматики. Код выхода не меняет |
| `omitted: ast - missing` / `unavailable`, код 1 | фейковый GitHub не отдал файл — ошибка песочницы, а не файла |
| `omitted: ast - too_large` / `file_limit` | файл больше `AST_MAX_FILE_BYTES` / сверх `AST_MAX_FILES` файлов PR |
| `omitted: scopes - budget` | AST не влез в пакет — `--context-max-tokens` больше |
| `omitted: hunk:N - budget`, охват `partial` / `not_reviewed` | дифф файла не влез целиком |
| `AST не выбирался` у файла больше 8 КБ с правкой в функции и охватом `reviewed` / `partial` | так быть не должно — смотрите `--full-outline`: есть ли там определение вокруг правки |

## Тесты

`tests/sandbox/outline/test_outline.py` — часть обычного `uv run pytest` и идёт в CI.
