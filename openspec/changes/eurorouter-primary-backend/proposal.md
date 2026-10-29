## Why

Стенд не ревьюит настоящей моделью: `deploy.yml` выбирает `LLM_BACKEND=stub`, потому что Ollama на стенде нет, а Eurorouter по спеке `llm-gateway` подключается только фоллбэком после локальной модели. Даже с работающими вебхуками прогон публикует заглушку. Задача — larchanka-training/dmc-268-api-t1#245.

## What Changes

- `llm-gateway`: требование «Локальная модель основная, hosted-провайдер только явным фоллбэком» переименовано и расширено — hosted-провайдер получает код только по явному выбору: фоллбэком после локальной модели (как раньше) или основным и единственным через `LLM_BACKEND=eurorouter`. Без явного выбора код по-прежнему не покидает нашу сеть.
- `app/config.py`: значение `eurorouter`; обязательны `EUROROUTER_BASE_URL`, `EUROROUTER_MODEL`, `EUROROUTER_API_KEYS`; `LLM_HOSTED_FALLBACK` с ним не сочетается — фоллбэк остаётся только после локальной модели.
- `build_llm_gateway`: Eurorouter единственным провайдером; лог старта называет, что код уходит hosted-провайдеру.
- `backend-delivery`: развёртывание отклоняет `llm_backend = "eurorouter"` без реквизитов Eurorouter до изменений на сервере. `infra/variables.tf` и тесты OpenTofu.
- `deploy.yml`: `llm_backend = eurorouter`; адрес и ключи — `vars.AI_DMC268_URL` и `secrets.AI_DMC268_T1`, те же, что у `eval.yml`; модель — из `.github/eurorouter-model`.
- `webhook-intake`: ревью только для PR своих авторов (`author_association` — `OWNER`, `MEMBER`, `COLLABORATOR`). Иначе любой пользователь GitHub тратил бы ключ модели PR'ами из форка в публичный репозиторий. Временно, до биллинга.
- Документация: `README.md`, `infra/README.md`, `.env.example`.

## Capabilities

### New Capabilities

### Modified Capabilities

- `llm-gateway`: hosted-провайдер может быть выбран основным.
- `backend-delivery`: проверка реквизитов Eurorouter при выборе его основным.
- `webhook-intake`: ревью только для PR своих авторов.

## Impact

- Код подключённых репозиториев уходит Eurorouter на каждом прогоне стенда — осознанное решение (#245).
- Каждый прогон тратит ключ `AI_DMC268_T1`. Лимитов и проверки баланса нет: квоты — тема следующего спринта, стоимость и баланс — #119. Пока приложение ставится только в `larchanka-training`, круг репозиториев ограничен.
- Временный сбой Eurorouter (429 на всех ключах, 502/503) роняет прогон сразу: повтора нет, запасного провайдера в этом режиме тоже. Ключ общий с `eval.yml` и `llm-live.yml`, поэтому их запуски тратят тот же лимит.
- Как остановить траты быстро: отозвать или сменить ключ `AI_DMC268_T1` у провайдера (сразу), затем выкатить стенд с `TF_VAR_llm_backend: stub`.
- Локальный стек и `eval.yml` не меняются: `stub` и `ollama` работают как раньше.
