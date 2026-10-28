# Песочница воркера

Локальный прогон настоящего воркера на мок-данных: тот же цикл потребления (`Worker.run`), тот же
пайплайн ревью (`run_review`) и промпты из `prompts/review/`, что в бою, — но без Docker,
PostgreSQL, RabbitMQ и GitHub. Нужна только локальная модель в Ollama. Подходит для ручной
отладки промптов, контекста и поведения при сбоях.

## Быстрый старт

Из корня репозитория:

```bash
make sandbox-demo
```

Одна команда проходит четыре шага и на каждом говорит, что делает:

| Шаг | Что происходит | Если не так |
|---|---|---|
| 1. Проверка окружения | `uv`, Python 3.14, `git`, `curl`, `ollama`, место на диске, отвечает ли Ollama и скачана ли модель | печатает ✗ и команду, которая исправит; дальше не идёт |
| 2. Зависимости | `uv sync --all-extras` | — |
| 3. Ollama и модель | запускает `ollama serve` в фоне, если она не отвечает, и скачивает `MODEL` | лог — `.ollama.log` |
| 4. Прогон | воркер разбирает мок-PR [`examples/case.yaml`](examples/case.yaml) и печатает отчёт | итог и подсказка — последней строкой |

Первый запуск скачивает модель (`qwen2.5-coder:1.5b`, около 1 ГБ); повторные — секунды:
уже работающая Ollama и скачанная модель пропускаются.

Остановить Ollama, которую запустил `make`, — `make sandbox-down`. Свою, запущенную не через
`make`, эта цель не трогает. Только проверить окружение, ничего не меняя, — `make sandbox-doctor`.

Ставить заранее нужно только [uv](https://docs.astral.sh/uv/) и [Ollama](https://ollama.com/download)
(на macOS — `brew install uv ollama`); остальное проверка подскажет сама.

### Как читать отчёт

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
берите модель крупнее: `make sandbox-demo MODEL=qwen2.5-coder:7b`.

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
make sandbox-demo DEMO_CASE=my-case.yaml
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
| `make sandbox-oracle` | ровно размеченными нарушениями кейса (`expected`) |
| `make sandbox-script` | по сценарию из JSON (`SCRIPT`, кейс — `CASE_FILE`) |

Сценарий модели — JSON-список записей, по одной на вызов модели
(пример — [`examples/llm-script.json`](examples/llm-script.json)):

```json
[
  {"review": [{"path": "app/billing/discount.py", "line": 7, "message": "...",
               "category": "correctness", "severity": "high"}], "tokens": 1200},
  {"verify": [["F1", "keep"]], "tokens": 300},
  {"error": "output"}
]
```

- `review` — ответ ревью: замечания `{path, line, message, category?, severity?, rule?: [имя, пункт], side?}` и необязательная `summary`;
- `verify` — вердикты верификатора `keep` / `drop` по кандидатам `F1`, `F2`, … в порядке после отсева;
- `error` — отказ модели: `output`, `timeout`, `rate_limited`, `unavailable`. На ревью он роняет прогон (`failed`), на верификации кандидаты публикуются без неё.

Записи расходуются по порядку: на каждый PR — запись ревью, затем запись верификации, но только
если после отсева остался хоть один кандидат. Записи кончились — `AssertionError` на лишнем вызове,
сообщение уходит в nack.

## Сбои GitHub и очереди

Сбои фейкового GitHub — `FAULTS` у любой цели песочницы (через пробел) или `--github-fault`:

```bash
make sandbox-demo FAULTS="review-502-stored latency=0.2"
```

| Сбой | Что делает |
|---|---|
| `review-502-stored` | `POST …/reviews` отвечает 502, хотя ревью записано: повтор не должен опубликовать дубликат |
| `review-502` | 502 до записи ревью, повтор публикует |
| `review-rate-limit` | 429 вторичного лимита с `Retry-After: 1` |
| `graphql-500` | каждый запрос GraphQL (треды) отвечает 500 |
| `review-comments-500` | `GET …/reviews/{id}/comments` отвечает 500 |
| `foreign-review` | на каждом PR уже есть ревью человека |
| `latency=SEC` | задержка SEC секунд перед каждым ответом |

Отказы `review-*` встают в одну очередь на все PR, по одному на запрос `POST …/reviews`; чтобы
отказать дважды, сбой задают дважды.

Очередь — флаги через `ARGS`:

- `--redeliver` — каждое сообщение PR приходит повторно, как после потерянного ack; после nack повтора нет — сообщение уже в DLQ;
- `--message <путь>` — сырое тело сообщения, доставляется после PR как есть (пример битого — [`examples/bad-message.json`](examples/bad-message.json)).

```bash
make sandbox-oracle CASE=04-users-sql-injection FAULTS=review-502-stored ARGS=--redeliver
```

## Все параметры

Переменные `make`:

| Переменная | По умолчанию | Смысл |
|---|---|---|
| `MODEL` | `$OLLAMA_MODEL` или `qwen2.5-coder:1.5b` | модель Ollama |
| `OLLAMA_URL` | `$OLLAMA_BASE_URL` или `http://localhost:11434` | адрес Ollama |
| `DEMO_CASE` | `tests/sandbox/examples/case.yaml` | мок-PR для `sandbox-demo` |
| `CASE` | все кейсы | кейс корпуса для `sandbox`, `sandbox-stub`, `sandbox-oracle` |
| `CASE_FILE`, `SCRIPT` | файлы из `examples/` | кейс и сценарий для `sandbox-script` |
| `RANGE` | `develop..HEAD` | диапазон git для `sandbox-git` |
| `RULES` | `rules` | содержимое `.review/rules.md`: `absent`, `rules`, `empty`, `adversarial` |
| `FAULTS` | — | сбои GitHub через пробел |
| `ARGS` | — | любые ключи CLI ниже |

Ключи CLI (`uv run python -m tests.sandbox --help`):

- `--llm {ollama,stub,oracle,script}` — модель, по умолчанию `ollama`; `--llm-script <путь>` — сценарий для `script`;
- `--model`, `--ollama-url` — модель и адрес Ollama;
- `--case <id>`, `--case-file <путь>`, `--from-git <BASE..HEAD>` (и `--repo <каталог>`) — входные PR, можно повторять; собираются в этом порядке. Без них и без `--message` берутся все кейсы корпуса;
- `--message <путь>`, `--redeliver`, `--github-fault <сбой>` — сбои, см. выше;
- `--rules {absent,rules,empty,adversarial}` — содержимое `.review/rules.md`;
- `--window`, `--context-max-tokens` — под окно маленькой модели: если полный пакет в окно не влезает, воркер не стартует;
- `--json <путь>` — весь результат прогона в JSON;
- `-v` — логи воркера.

Коды выхода:

- `0` — все PR дошли до `completed`, сообщения подтверждены;
- `1` — сообщение какого-то PR ушло в nack или прогон не `completed`; сырые сообщения (`--message`) в код не входят — их дело быть отвергнутыми;
- `2` — вход не собрать (нет файла, не тот диапазон git, битый сценарий, неизвестный сбой), Ollama недоступна, модель не скачана или окно не подходит.

## Что пошло не так

| Симптом | Что сделать |
|---|---|
| `✗ uv не установлен` / `✗ ollama не установлена` | команда установки — строкой ниже в выводе проверки |
| `✗ запуск не из корня репозитория` | `cd` в корень репозитория |
| `Ollama не ответила за 30 с` | смотрите `.ollama.log`; порт занят — другой адрес: `OLLAMA_URL=http://localhost:11500` |
| код 2, «модель не скачана» | `make ollama-pull MODEL=...` или повторите `make sandbox-demo` |
| код 2, про окно контекста | `ARGS="--window 32768"` или модель с бо́льшим окном |
| `verifying: failed`, `LLM_INVALID_OUTPUT` | модель ответила не по формату — возьмите крупнее |
| код 1, `nack` | причина — в отчёте; логи воркера — `ARGS=-v` |

## Тесты песочницы

Тесты самой песочницы — `tests/sandbox/test_*.py` — часть обычного `uv run pytest` и идут в CI.
Ручной прогон с моделью в CI не входит.
