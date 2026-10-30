## Why

Калибровка модели стенда (larchanka-training/dmc-268-api-t1#267) выбрала `glm-5.3`, но только с `reasoning_effort: low` в запросе. Без параметра модель тратит предел ответа (4096 токенов) на размышления: на корпусе `tests/eval/rules` 7 из 28 прогонов упали с обрезанным ответом, eval шёл 28 минут. С `low` — 0 из 28 упавших, eval за 4 минуты, Recall 0.89–1.00 против 0.67–0.78 у текущей `qwen3-coder-next-80b` при Precision 0.73–1.00 против 0.86–1.00. На корпусе из 8 кейсов с тремя повторами — 0 из 96 упавших. Сейчас параметр задать нечем: в калибровочной ветке он был захардкожен.

## What Changes

- `llm-gateway`: необязательный параметр рассуждения hosted-провайдера задаётся конфигурацией и уходит в запрос, только когда задан. Каталог Eurorouter отдаёт `reasoning_effort` не у каждой модели (у `qwen3-coder-next-80b` его нет), а неизвестный параметр шлюз может отвергнуть.
- `app/config.py`: `EUROROUTER_REASONING_EFFORT` — `low`, `medium`, `high` или пусто; пустая строка — не задано.
- `OpenAiCompatibleGateway`: `reasoning_effort` в теле запроса только при заданном значении; контейнер, live-тесты и eval передают его из настроек.
- Развёртывание: файл `.github/eurorouter-reasoning-effort` рядом с `.github/eurorouter-model`, его читают `infra.sh`, `plan.sh`, `eval.yml` и `llm-live.yml`; переменная OpenTofu `eurorouter_reasoning_effort` с проверкой значения; у воркера `EUROROUTER_REASONING_EFFORT` только при непустом значении.
- `.github/eurorouter-model` — `glm-5.3`, `.github/eurorouter-reasoning-effort` — `low`.
- Документация: `README.md`, `infra/README.md`, `.env.example`, `docker-compose.yml`.

## Capabilities

### New Capabilities

### Modified Capabilities

- `llm-gateway`: параметр рассуждения hosted-провайдера.

## Impact

- Стенд, `llm-live` и eval переходят на `glm-5.3`: по #267 она находит больше, но шумнее (лишних замечаний 6 против 1 на 7 кейсах) и дороже — около $0.022 за прогон против $0.008.
- Файл `.github/eurorouter-reasoning-effort` обязателен: без него `infra.sh` и `plan.sh` падают, как без файла модели. Пустой файл — параметр не отправляется.
- Откат — вернуть в `.github/eurorouter-model` прежнюю модель и очистить `.github/eurorouter-reasoning-effort`.
