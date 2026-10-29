## 1. Код

- [x] 1.1 `app/config.py`: `llm_backend` принимает `eurorouter`; без `EUROROUTER_BASE_URL`, `EUROROUTER_MODEL`, `EUROROUTER_API_KEYS` — ошибка с именами; `LLM_HOSTED_FALLBACK` с `eurorouter` — ошибка; худший случай вызова — один провайдер
- [x] 1.2 `build_llm_gateway`: Eurorouter единственным провайдером в `FallbackLlmGateway`, лог старта называет hosted-провайдера; сообщения «LLM_BACKEND не задан» перечисляют три значения
- [x] 1.3 Тесты: `tests/test_config.py`, `tests/test_container.py`

## 2. Развёртывание

- [x] 2.1 `infra/variables.tf`: `llm_backend` принимает `eurorouter`, проверка реквизитов Eurorouter; тесты `infra/tests/main.tftest.hcl`
- [x] 2.2 `.github/workflows/deploy.yml`: `TF_VAR_llm_backend: eurorouter`, адрес и ключи из `AI_DMC268_URL` / `AI_DMC268_T1`, модель из `.github/eurorouter-model`
- [x] 2.3 `README.md`, `infra/README.md`, `.env.example`, комментарий в `infra/app.tf`

## 3. Стенд

- [ ] 3.1 После выката лог старта воркера называет Eurorouter и модель из `.github/eurorouter-model`
