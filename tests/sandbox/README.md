# Песочница воркера

Локальный прогон настоящего воркера на мок-данных: тот же цикл потребления (`Worker.run`), тот же
пайплайн ревью (`run_review`) и промпты из `prompts/review/`, что в бою, — но без Docker,
PostgreSQL, RabbitMQ и GitHub. Нужна только локальная модель в Ollama. Подходит для ручной
отладки промптов и контекста. Сбои GitHub и повторная доставка сообщений здесь не моделируются —
их проверяют тесты воркера (`tests/worker/`) и e2e (`tests/e2e/`).

## Быстрый старт

Из корня репозитория:

```bash
uv sync --all-extras
make sandbox CASE=04-users-sql-injection
```

`make sandbox` запускает `ollama serve` в фоне, если Ollama не отвечает на `OLLAMA_URL`, скачивает
`MODEL`, если её ещё нет, и прогоняет воркер на кейсе корпуса (без `CASE` — на всех кейсах). То же
по шагам: `make ollama-up ollama-pull`, затем прогон. Запущенная так Ollama остаётся работать в фоне,
её лог — `.ollama.log`.

Первый запуск скачивает модель (`qwen2.5-coder:1.5b`, около 1 ГБ); повторные — секунды:
уже работающая Ollama и скачанная модель пропускаются.

Ставить заранее нужно [uv](https://docs.astral.sh/uv/) и [Ollama](https://ollama.com/download)
(на macOS — `brew install uv ollama`).

### Как читать отчёт

Пример — прогон мок-PR [`examples/case.yaml`](examples/case.yaml) (`make sandbox ARGS="--case-file tests/sandbox/examples/case.yaml"`):

```text
=== [1] case: Скидка на заказ - нет проверки границы процента
статус: completed; сообщение: ack; модель: qwen2.5-coder:1.5b; длительность: 8.55 с
шаги:
  - fetching_diff: completed, 0.05 с
  - analysing: completed, 6.21 с (токенов 4732)
  - verifying: completed, 2.20 с (токенов 2091)
  - publishing: completed, 0.09 с
опубликовано ревью: 1, inline-комментариев: 1
  app/billing/discount.py:7 [correctness] Скидка больше 100% даёт отрицательную цену ...
ожидалось:
  app/billing/discount.py:7 [correctness] case/discount-over-100
```

- **статус** — чем кончился прогон ревью (`completed`, `failed`); **сообщение** — `ack` или `nack` из очереди;
- **шаги** — этапы пайплайна с таймингом, токенами и кодом ошибки, если этап упал;
- **опубликовано** — что воркер отправил бы в PR (на деле — в фейковый GitHub);
- **ожидалось** — размеченные в кейсе нарушения, чтобы сравнить глазами.

Маленькая модель часто промахивается или отвечает не по формату (`verifying: failed`,
`LLM_INVALID_OUTPUT`) — это результат прогона, а не поломка песочницы. Для осмысленных замечаний
берите модель крупнее: `make sandbox MODEL=qwen2.5-coder:7b CASE=04-users-sql-injection`.

## Что настоящее, а что подменено

| Компонент | В песочнице |
|---|---|
| Цикл воркера, пайплайн ревью, промпты, контекст, tree-sitter | настоящие |
| Модель | настоящая локальная Ollama (или заглушка — см. «Режимы без модели») |
| База | в памяти, `FakeUnitOfWork` |
| Очередь | фейковый канал, `tests/fakes/queue.py` |
| GitHub | `FakeGitHub` за настоящими `GitHubAppAuth` и `GitHubVcsGateway` |

## Свои мок-данные

**Свой PR.** Скопируйте [`examples/case.yaml`](examples/case.yaml) и поправьте:

```yaml
title: Название кейса для отчёта
pull_request:
  title: "feat: заголовок PR"
  description: Описание PR.
files:                        # путь → полное содержимое файла после правки
  app/billing/discount.py: |
    def apply_discount(price: int, d: int) -> int:
        return price - price * d // 100
expected:                     # необязательно: что ревью должно найти
  - id: discount-over-100
    path: app/billing/discount.py
    contains: "return price - price * d // 100"   # строка, на которой ждём замечание
    category: correctness
```

```bash
make sandbox ARGS="--case-file my-case.yaml"     # на Ollama
make sandbox-script CASE_FILE=my-case.yaml       # со сценарием модели SCRIPT
```

**Кейс из корпуса.** Готовые PR с размеченными нарушениями лежат в
[`tests/eval/rules/corpus/`](../eval/rules/corpus/); имя файла без `.yaml` — идентификатор:

```bash
make sandbox CASE=04-users-sql-injection    # один кейс на Ollama
make sandbox                                # все кейсы корпуса
```

**Свои изменения из git.** PR собирается из диапазона коммитов — дифф от merge-base, как на GitHub:

```bash
make sandbox-git RANGE=develop..HEAD
```

## Режимы без модели

Офлайн, без Ollama — когда проверяете обвязку, а не ответы модели:

| Цель | Модель отвечает |
|---|---|
| `make sandbox-stub` | пустыми ответами |
| `make sandbox-oracle` | ровно размеченными нарушениями кейса (`expected`); кейс узнаёт по пути файла, поэтому пути файлов в PR одного прогона не должны повторяться — иначе код 2 |
| `make sandbox-script` | по сценарию из JSON (`SCRIPT`, кейс — `CASE_FILE`) |

Сценарий модели — JSON-список записей, по одной на вызов модели
(пример — [`examples/llm-script.json`](examples/llm-script.json)):

```json
[
  {"review": [{"filePath": "app/billing/discount.py", "line": 7, "message": "...",
               "category": "correctness", "severity": "high",
               "suggestion": {"diff": "..."}}], "tokens": 1200},
  {"verify": [{"id": "F1", "verdict": "keep", "reason": "..."}], "tokens": 300},
  {"error": "output"}
]
```

Запись — ровно один из ключей `review`, `verify`, `error` и необязательный `tokens` (целое от 0):

- `{"review": [<замечание>, …], "summary"?: {…}}` — замечания в форме ответа модели
  (`app/application/llm_schema.py`, camelCase): `filePath`, `line`, `message` обязательны;
  `side` (`new`), `category` (`readability`), `severity` (`low`), `confidence` (`null`) — по
  умолчанию; `suggestion: {"diff": "…"}` и `rule: {"name", "check"}` — по желанию. Без `summary` —
  сводка-заглушка;
- `{"verify": [{"id": "F1", "verdict": "keep" | "drop", "reason"?: "…"}, …]}` — кандидаты
  `F1`, `F2`, … в порядке после отсева;
- `{"error": "output" | "timeout" | "rate_limited" | "unavailable"}` — отказ модели: на ревью
  роняет прогон (`failed`), на верификации кандидаты публикуются без неё.

Ответ модели можно вставить целиком, как она его вернула: `{"review": {"schemaVersion": "1",
"findings": […], "summary": {…}}}` или `{"verify": {"schemaVersion": "1", "verdicts": […]}}`.
Проверяют те же модели, что и ответ настоящей модели: лишний ключ, чужое значение или неверный
тип — ошибка с номером записи и местом в ней (`запись 0: … findings.1.severity`).

Записи расходуются по порядку: на каждый PR — запись ревью, затем запись верификации, но только
если после отсева остался хоть один кандидат. Записи кончились — `AssertionError` на лишнем вызове,
сообщение уходит в nack.

## Все параметры

Переменные `make`:

| Переменная | По умолчанию | Смысл |
|---|---|---|
| `MODEL` | `$OLLAMA_MODEL` или `qwen2.5-coder:1.5b` | модель Ollama |
| `OLLAMA_URL` | `$OLLAMA_BASE_URL` или `http://localhost:11434` | адрес Ollama |
| `CASE` | все кейсы | кейс корпуса для `sandbox`, `sandbox-stub`, `sandbox-oracle` |
| `CASE_FILE`, `SCRIPT` | файлы из `examples/` | кейс и сценарий для `sandbox-script` |
| `RANGE` | `develop..HEAD` | диапазон git для `sandbox-git` |
| `RULES` | `rules` | содержимое `.review/rules.md`: `absent`, `rules`, `empty`, `adversarial` |
| `ARGS` | — | любые ключи CLI ниже |

Ключи CLI (`uv run python -m tests.sandbox --help`):

- `--llm {ollama,stub,oracle,script}` — модель, по умолчанию `ollama`; `--llm-script <путь>` — сценарий для `script`;
- `--model`, `--ollama-url` — модель и адрес Ollama;
- `--case <id>`, `--case-file <путь>`, `--from-git <BASE..HEAD>` (и `--repo <каталог>`) — входные PR, можно повторять; собираются в этом порядке. Без них берутся все кейсы корпуса;
- `--rules {absent,rules,empty,adversarial}` — содержимое `.review/rules.md`;
- `--window`, `--context-max-tokens`, `--max-output-tokens` — под окно маленькой модели: если полный пакет вместе с потолком ответа в окно не влезает, воркер не стартует;
- `--json <путь>` — весь результат прогона в JSON;
- `-v` — логи воркера.

Коды выхода:

- `0` — все PR дошли до `completed`, сообщения подтверждены;
- `1` — сообщение какого-то PR ушло в nack или прогон не `completed`;
- `2` — вход не собрать (нет файла, битый YAML кейса, не тот диапазон git, битый сценарий, повтор пути файла с `--llm oracle`), Ollama недоступна, модель не скачана или окно не подходит.

Неожиданные запросы к GitHub (маршрут, которого фейк не знает) печатаются в отчёте и считаются в
итоге, но в код выхода не входят: песочница — для ручной отладки.

## Что пошло не так

| Симптом | Что сделать |
|---|---|
| `ollama не установлена` | `brew install ollama` или [ollama.com/download](https://ollama.com/download) |
| `Ollama не ответила за 30 с` | смотрите `.ollama.log`; порт занят — другой адрес: `OLLAMA_URL=http://localhost:11500` |
| код 2, «модель не скачана» | `make ollama-pull MODEL=...` или повторите `make sandbox` |
| код 2, про окно контекста | `ARGS="--window 32768"` или модель с бо́льшим окном |
| `verifying: failed`, `LLM_INVALID_OUTPUT` | модель ответила не по формату — возьмите крупнее |
| код 1, `nack` | причина — в отчёте; логи воркера — `ARGS=-v` |

## Тесты песочницы

Тесты самой песочницы — `tests/sandbox/**/test_*.py` — часть обычного `uv run pytest` и идут в CI.
Ручной прогон с моделью в CI не входит.

Что tree-sitter отдаёт модели из AST, без модели и без сети, —
[`outline/README.md`](outline/README.md) (`make sandbox-outline-demo`).
