# Proposal

## Why

Первый запуск песочницы воркера — несколько ручных шагов: проверить, что стоят uv, Python 3.14 и
Ollama, сделать `uv sync --all-extras`, поднять Ollama, скачать модель и только потом прогнать кейс.
Где именно не подошло окружение, видно только по ошибке на середине пути.

Цели `make sandbox-demo`, `sandbox-doctor`, `sandbox-down` и скрипт `scripts/sandbox-doctor.sh`
были в #229, но на ревью их вынесли из него в отдельную задачу — #237. Этот change их возвращает:
первый прогон — одной командой, по образцу `make sandbox-outline-demo`.

## What Changes

- `make sandbox-demo` — «начать отсюда»: проверка окружения (`scripts/sandbox-doctor.sh`),
  `uv sync --all-extras`, `make ollama-up ollama-pull` и прогон воркера на мок-PR `DEMO_CASE`
  (по умолчанию `tests/sandbox/examples/case.yaml`). Код `0` — прогон `completed`, сообщение
  `ack`; любой сбой — код make `2` (GNU make не передаёт код рецепта), а причину — прогон не
  завершился или `nack`, вход или окружение не подошли — называет последняя строка. Коды
  `0`/`1`/`2` различает прямой запуск `uv run python -m tests.sandbox`.
- `make sandbox-doctor` и `scripts/sandbox-doctor.sh` — проверка окружения без изменений: корень
  репозитория, uv и Python 3.14, git, curl, ollama, отвечает ли Ollama на `OLLAMA_URL`, скачана ли
  `MODEL`, не меньше 3 ГБ свободного места там, куда Ollama кладёт модели (`OLLAMA_MODELS`, по
  умолчанию `~/.ollama/models`). `✗` — ошибка (скрипт — код 1, `make sandbox-doctor` — код make
  2), `!` — предупреждение.
- `make sandbox-down` останавливает `ollama serve`, запущенную `make ollama-up`; Ollama, запущенную
  не через make, не трогает.
- `make ollama-up` пишет PID запущенной Ollama в `.ollama.pid`; файл — в `.gitignore`.
- README песочницы и корневой README: быстрый старт начинается с `make sandbox-demo`.

**Не входит:** шаг `sandbox-demo` в CI — прогон с моделью остаётся ручным; тесты doctor идут в
обычном `uv run pytest`.

## Capabilities

### New Capabilities

Нет.

### Modified Capabilities

Нет.

`skip_specs: true`: наблюдаемое поведение сервиса не меняется — ни ручки, ни прогон ревью, ни
публикация. Меняются цели `Makefile`, скрипт разработчика, тесты песочницы и документация.

## Impact

- `Makefile`: цели `sandbox-demo`, `sandbox-doctor`, `sandbox-down`; `ollama-up` пишет `.ollama.pid`.
- `scripts/sandbox-doctor.sh`.
- `.gitignore`: `.ollama.pid`.
- `tests/sandbox/test_sandbox.py`: тесты doctor.
- `tests/sandbox/README.md`, `README.md`.
