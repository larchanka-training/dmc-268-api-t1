# Песочница tree-sitter

Офлайн-прогон этапа сборки контекста: настоящие `fetch_review_diff` → `build_review_context` на
мок-PR показывают, что tree-sitter нашёл в каждом файле, что выбрано вокруг изменённых строк, что
ушло в блок `ast` пакета для модели и что отброшено и почему. Без Ollama, Docker, PostgreSQL,
RabbitMQ и сети. Песочница воркера целиком — [`../README.md`](../README.md).

## Быстрый старт

Из корня репозитория:

```bash
make sandbox-outline-demo
```

| Шаг | Что происходит | Если не так |
|---|---|---|
| 1. Проверка окружения | корень репозитория, `uv`, Python 3.14, `git` (`scripts/sandbox-outline-doctor.sh`) | ✗ и команда, которая исправит; дальше не идёт |
| 2. Зависимости и грамматики | `uv sync --all-extras`, затем версии `tree-sitter` и четырёх грамматик и разбор образца на каждый язык (`doctor --grammars`) | ✗ на `tree-sitter` 0.26.0 (#226) и на битом колесе |
| 3. Разбор | мок-PR [`examples/outline-case.yaml`](examples/outline-case.yaml): Python, TypeScript, TSX, JavaScript, Go, Markdown | итог и подсказка — последними строками |

Повторный запуск — меньше секунды. Только проверить окружение —
`scripts/sandbox-outline-doctor.sh` (и `--grammars` после `uv sync`).

### Как читать отчёт

```text
песочница tree-sitter: PR 1, правила: rules, лимит пакета: 32000 токенов
=== [1] outline-case: Мультиязычный мок-PR
app/billing/discount.py  python  изменены строки 13
  контур: определений 3, импортов 1
  в контекст (как видит модель):
    5-13 class Discount
      11-13 def apply(price: Decimal, percent: int) -> Decimal
  импорты:
    from decimal import Decimal
...
docs/orders.md  без грамматики
пакет: файлов с AST: 6, ~643 токенов; весь пакет ~1899 из 32000; omitted: 0 из 0
проблемы: -
итого: PR 1, файлов с AST 6, проблем 0, время 0.1 с
```

- **изменены строки** — строки новой версии, которые затронул дифф (`touched_lines`);
- **контур** — сколько определений и импортов нашёл tree-sitter во всём файле; весь контур —
  `ARGS=--full-outline`;
- **в контекст (как видит модель)** — ровно текст элемента `scopes` блока `ast` пакета (без обёртки
  недоверенных данных): `начало-конец сигнатура`, вложенные — с отступом. Здесь правка в строке 13
  выбрала метод (с декоратора, строка 11) и его класс, а `__init__` — нет;
- **импорты** — элемент `imports` того же блока;
- строки статуса без контура: `без грамматики`, `отфильтрован` (lock-файлы, `vendor/`, сгенерированное),
  `удалён`, `нет hunk'ов`, `hunk'и не вошли в пакет по бюджету`, `AST пропущен: <причина>`,
  `отброшено бюджетом: scopes` — подробно в «Что пошло не так»;
- **пропущено AST / отброшено по бюджету** — записи `omitted` пакета: `путь - причина`,
  `путь:строка hunk`, у текста PR без пути — только часть (`pr_title`, `commit_message #2`);
- **пакет** — сколько файлов дали AST и его токены, размер всего пакета против лимита, сколько
  записей `omitted` вошло из общего числа (перечень тоже урезается бюджетом).

## Что настоящее, а что подменено

| Компонент | В песочнице |
|---|---|
| `fetch_review_diff`, `build_review_context`, бюджет пакета, `select_scopes` | настоящие |
| tree-sitter и грамматики (`TreeSitterDefinitionExtractor`) | настоящие |
| Лимиты контекста | из `Settings`, как у воркера (`context_limits`) |
| GitHub | `FakeGitHub` за настоящими `GitHubAppAuth` и `GitHubVcsGateway`; дифф — `difflib` |
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
  Без `LINES` файл новый и затронут целиком — выбираются все определения. `FILE` и `LINES`
  передаются только аргументами make (`make sandbox-outline FILE=… LINES=…`), окружение
  игнорируется: `LINES` выставляет терминал.
- **`RANGE`** — диапазон `BASE..HEAD`, дифф — от merge-base, как у PR; файлы больше 200 000 байт
  пропускаются.
- **`CASE_FILE`** — YAML в формате корпуса (`title`, `pull_request`, `files`) плюс необязательный
  ключ `lines:` — какие строки файла изменены:

  ```yaml
  lines:
    app/billing/discount.py: "13"
    web/api/handler.ts: "4, 11"
  ```

  Без `lines:` все файлы кейса новые. Тот же кейс годится и для `make sandbox CASE_FILE=…` —
  там ключ игнорируется.

Пример в Makefile — иллюстрация: правка файла сдвинет номера строк.

## Все параметры

Флаги — через `ARGS`: `make sandbox-outline ARGS="--full-outline --context-max-tokens 2048"`.

| Ключ | Что делает |
|---|---|
| `--case ID`, `--case-file PATH`, `--from-git RANGE` (+ `--repo DIR`), `--file PATH` | входы, повторяемые; без них — мок-PR |
| `--lines SPEC` | изменённые строки — только с одним `--file` |
| `--rules {absent,rules,empty,adversarial}` | `.review/rules.md` целевой ветки; занимает часть бюджета, как у воркера (по умолчанию `rules`) |
| `--context-max-tokens N` | потолок пакета; минимум 1024 |
| `--window N` | окно модели: на сборку не влияет, только повторяет отказ воркера с малым окном |
| `--full-outline` | весь контур файла, а не только выбор |
| `--payload` | вместо отчёта — JSON `ast`, `omitted`, `omitted_count` каждого PR; stdout — чистый JSON |
| `--json PATH` | весь результат в файл; строка «результат:» — в stdout, а с `--payload` — в stderr |
| `-v` | логи уровня INFO |

Остальные лимиты (`DIFF_MAX_BYTES`, `AST_MAX_FILES`, `AST_MAX_FILE_BYTES`, …) берутся из окружения
процесса: `DIFF_MAX_BYTES=100 make sandbox-outline`. `.env` не читается.

Коды выхода: `0` — всё разобрано (пустой контур — только предупреждение); `1` — у PR не получен
дифф, `parse_error` или файл не загружен (`missing`, `unavailable`); `2` — вход или настройки не
подходят (нет файла, плохой `--lines`, битый YAML, `--context-max-tokens` меньше 1024, малое окно)
или не записан `--json`; `139` — segfault процесса, не наш код.

## Что пошло не так

| Симптом | Что сделать |
|---|---|
| код 139, segfault | `tree-sitter` 0.26.0 (#226) — `uv sync --all-extras`; проверка — `scripts/sandbox-outline-doctor.sh --grammars` |
| `✗ … не установлен`, `✗ окружение Python не создано` | `uv sync --all-extras` |
| `parse_error` | экстрактор упал на файле — код 1; файл и причина в «проблемы» |
| синтаксическая ошибка в файле | не ошибка песочницы: tree-sitter разбирает частично, статус `in_context`, `parse_error` нет. Определение может пропасть (незакрытая скобка) или сигнатура захватит кусок тела (висящий оператор) |
| `предупреждение: пустой контур` | в непустом файле нет ни определений, ни импортов: нормально для `__init__.py` из docstring или модуля констант; на файле с `def`/`class` — сломан запрос грамматики. Код выхода не меняет |
| `AST пропущен: missing` / `unavailable`, код 1 | фейковый GitHub не отдал файл — ошибка песочницы, а не файла |
| `AST пропущен: too_large` / `file_limit` | файл больше `AST_MAX_FILE_BYTES` / сверх `AST_MAX_FILES` файлов PR |
| `отброшено бюджетом: scopes` | AST не влез в пакет — `--context-max-tokens` больше |
| `hunk'и не вошли в пакет по бюджету` | дифф файла не влез, AST для него не выбирался |
| `нет hunk'ов` | пустой новый файл или смена режима — выбирать нечего |
| в диффе строки `<песочница: строка N>` | так задаются `LINES` и `lines:`: в базе эти строки заменены заглушками, чтобы дифф затронул ровно их. Hunk из-за этого немного дороже по бюджету |

## Тесты

`tests/sandbox/outline/test_outline.py` — часть обычного `uv run pytest` и идёт в CI.
