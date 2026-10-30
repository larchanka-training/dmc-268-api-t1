## 1. Код

- [x] 1.1 `app/config.py`: `eurorouter_reasoning_effort` — `low`, `medium`, `high` или не задано, пустая строка — не задано
- [x] 1.2 `OpenAiCompatibleGateway`: `reasoning_effort` в теле запроса только при заданном значении; `build_llm_gateway` передаёт его из настроек
- [x] 1.3 Тесты: `tests/infrastructure/test_openai_compatible_gateway.py`, `tests/test_config.py`, `tests/test_container.py`; live-тесты и eval передают параметр

## 2. Развёртывание

- [x] 2.1 `.github/eurorouter-reasoning-effort` (`low`), его читают `infra.sh`, `plan.sh`, `eval.yml`, `llm-live.yml`; `.github/eurorouter-model` — `glm-5.3`
- [x] 2.2 `infra/variables.tf`: `eurorouter_reasoning_effort` с проверкой; `infra/app.tf`: env воркера только при непустом значении; тесты `infra/tests/main.tftest.hcl`
- [x] 2.3 `docker-compose.yml`, `.env.example`, `README.md`, `infra/README.md`

## 3. Стенд

- [ ] 3.1 После выката прогон на стенде доходит до публикации, в прогоне записана модель `glm-5.3`
