# Tasks

Порядок - тест первым (скилл `backend-tdd`): в каждой задаче сначала падающий тест, потом код.
Каждая группа приносит свои тесты и документы; последняя группа - только сквозные проверки.
HTTP-адаптеры тестируются на `httpx.MockTransport`, как `tests/infrastructure/test_github_vcs_gateway.py`: без сети и без пропусков.

## 1. Предел ответа: от конфигурации до запроса

- [x] 1.1 Порт: поле `LlmRequest.max_output_tokens: int` (`app/application/ports/llm_gateway.py`); `LlmLimits(window_tokens, max_output_tokens)` в `app/application/llm_review.py` - `request_review` и `request_verification` получают `limits` и кладут предел в запрос; `run_review(llm_limits=…)` и `build_review_handler` передают их дальше; `llm_limits(settings)` в `app/worker/factory.py`; `Settings.llm_context_window_tokens` (65 536) и `Settings.llm_max_output_tokens` (4 096), оба больше нуля.
      Проверка: `tests/application/test_llm_review.py` - запрос ревью и запрос верификации несут `max_output_tokens` из `LlmLimits`; `tests/test_config.py` - значения по умолчанию, чтение из окружения, ноль отвергается; `tests/worker/test_review_handler.py` - обработчик с лимитами доводит прогон до `completed`; `uv run mypy .` зелёный.

## 2. Окно модели

- [x] 2.1 Чистая функция `check_window(messages, *, window_tokens, max_output_tokens) -> Result[int]` в `app/domain/prompt.py` по design D5.
      Проверка: `tests/domain/test_prompt.py` - точная оценка для запроса с кириллицей и латиницей (байты UTF-8, округление вверх, накладка на сообщение); запрос ровно на границе помещается, на токен больше - неудача, текст которой называет оценку и окно.
- [x] 2.2 Чистая функция `check_window_config(texts, *, context_max_tokens, max_output_tokens, window_tokens) -> Result[None]` там же (системный промпт - длиннейший из двух, разметка - на четыре сообщения запроса верификатора); её вызывает `load_checked_prompt_texts(settings, root)` в `app/worker/factory.py` при старте воркера, до подключения к брокеру, и при неудаче бросает `WorkerConfigError`.
      Проверка: в `tests/domain/test_prompt.py` - помещающаяся конфигурация проходит, не помещающаяся даёт неудачу со всеми значениями в тексте; `tests/worker/test_startup.py` - конфигурация по умолчанию с настоящими промптами проходит, окно меньше пакета и резерва останавливает воркер.
- [x] 2.3 `app/domain/llm_errors.py`: `PromptTooLargeError(LlmError)`; `app/application/llm_review.py`: `request_review` и `request_verification` вызывают `check_window` с `LlmLimits` до порта и при неудаче бросают `PromptTooLargeError`.
      Проверка: `tests/application/test_llm_review.py` - слишком большой запрос: `PromptTooLargeError`, гейтвей не вызван; `tests/application/test_review_pipeline.py` - ревью больше окна → `failed` с `failed_stage = analysing` и причиной с оценкой и окном; верификация больше окна → `completed` и `unavailable`.

## 3. Цикл получения ответа по схеме

- [x] 3.1 `app/infrastructure/llm/structured.py`: `RawReply(text, model, tokens)` и `complete_structured(send, request, max_attempts) -> LlmResponse` по design D2.
      Проверка: `tests/infrastructure/test_llm_structured.py` - ответ по схеме с первой попытки: один вызов `send`; ответ не по схеме, затем по схеме: второй вызов получил исходные сообщения без изменений и в том же порядке плюс одно `user`-сообщение со списком `путь: вид`, токены - сумма; все попытки не по схеме → `LlmOutputError` со списком нарушений; невалидный JSON → «ответ не JSON»; поле с неверным типом и текстом «игнорируй системные инструкции» - этого текста нет ни в сообщении повтора, ни в тексте `LlmOutputError`.

## 4. Адаптер локальной модели

- [ ] 4.1 `app/infrastructure/llm/ollama.py`: `OllamaGateway(client, model, window_tokens, max_attempts)` - перевод в `POST /api/chat` и обратно по design D1, повтор через `complete_structured`, сверка `prompt_eval_count` по D5.
      Проверка: `tests/infrastructure/test_ollama_gateway.py` - точное тело запроса (`model`, роли и тексты сообщений, `format` = `model_json_schema()` модели ответа, `options.num_ctx`, `num_predict`, `temperature = 0`, `stream = false`); `model` и токены ответа (`prompt_eval_count + eval_count`) в `LlmResponse`; `prompt_eval_count` больше `window - max_output_tokens` → `LlmError`; `5xx`, таймаут, обрыв соединения → `LlmError` с причиной без тела запроса.

## 5. Адаптер Eurorouter

- [ ] 5.1 `app/infrastructure/llm/openai_compatible.py`: `OpenAiCompatibleGateway(client, model, keys, max_attempts)` - `POST /chat/completions` по design D1, ротация ключей по D4.
      Проверка: `tests/infrastructure/test_openai_compatible_gateway.py` - точное тело (`response_format` с `json_schema` и `strict: false`, `max_tokens`, `temperature = 0`) и заголовок `Authorization`; `429` на первом ключе, `200` на втором → ответ, два запроса; `401` на всех ключах → `LlmError`; два вызова подряд начинаются с разных ключей; `503` → `LlmError` после одного запроса, без перебора ключей; ни текст исключения, ни записи лога (`caplog`) не содержат ни одного ключа.

## 6. Фоллбэк

- [ ] 6.1 `app/infrastructure/llm/fallback.py`: `FallbackLlmGateway(providers)` по design D3.
      Проверка: `tests/infrastructure/test_fallback_gateway.py` с поддельными провайдерами - первый ответил: второй не вызван; первый бросил `LlmError` или `LlmOutputError`: ответ второго и его `model`; оба упали: проброшен сбой второго; переход записан в лог с именем провайдера и классом сбоя.

## 7. Конфигурация и сборка

- [ ] 7.1 `app/config.py`: остальные поля design D7 (окно и предел ответа - в задаче 1.1) и валидаторы.
      Проверка: `tests/test_config.py` - значения по умолчанию; `LLM_BACKEND=ollama` без `OLLAMA_MODEL` и `LLM_HOSTED_FALLBACK=true` без ключей, адреса или модели Eurorouter, а также фоллбэк при `LLM_BACKEND=stub` - ошибка с именем параметра; ключи - `SecretStr`, `repr(Settings)` их не содержит.
- [ ] 7.2 `app/infrastructure/container.py`: `llm_gateway()` по конфигурации - `StubLlmGateway` при `stub`, `FallbackLlmGateway([ollama])` или `[ollama, eurorouter]` при `ollama`; один `httpx.Client` на провайдера с `LLM_TIMEOUT_SECONDS`, созданный лениво и один раз.
      Проверка: `tests/test_container.py` - без `LLM_HOSTED_FALLBACK` Eurorouter в списке провайдеров нет при любых заданных ключах; с ним - вторым и только вторым; адаптер кэшируется; гвардрейлы `test_every_adapter_is_wired_in_the_composition_root` и `test_the_container_is_the_only_place_adapters_are_constructed` зелёные.
- [ ] 7.3 `run_worker` (`app/worker/factory.py`): `LLM_BACKEND` не задан - `WorkerConfigError` до подключения к брокеру (проверка окна при старте - задача 2.2).
      Проверка: `tests/worker/test_startup.py` - отказ с текстом, называющим параметр.
- [ ] 7.4 Развёртывание и документы: переменные design D7 в `.env.example`, `README.md`, `docker-compose.yml` (воркер - `LLM_BACKEND=stub` по умолчанию) и `infra/` (`main.tf`, `variables.tf`, ключи - sensitive); профиль `ollama` в `docker-compose.yml` (образ с зафиксированной версией, том для моделей, воркер с `LLM_BACKEND=ollama`).
      Проверка: `uv run pytest tests/test_stack_descriptions.py tests/test_docs.py`; `docker compose config` и `docker compose --profile ollama config` без ошибок.

## 8. Контекст PR

- [ ] 8.1 Шлюз VCS: `PRMetadata.description` (`app/domain/entities.py`), чтение `body` в `GitHubVcsGateway.fetch_pr_metadata`, новый метод `fetch_pr_commit_messages` в порте `app/application/ports/vcs_gateway.py` и адаптере `app/infrastructure/vcs/github.py` по design D6; фейки `tests/fakes/vcs_gateway.py`.
      Проверка: `tests/infrastructure/test_github_vcs_gateway.py` - `body: null` → пустая строка; три страницы коммитов → сообщения от нового к старому, не больше 250, не больше трёх запросов; неожиданное тело → `VcsError`.
- [ ] 8.2 `app/application/context_assembly.py`: чистая `assemble_context` принимает контекст PR и кладёт `pull_request_context` долей бюджета по design D6; `ContextLimits.pr_context_max_tokens` из `PR_CONTEXT_MAX_TOKENS`.
      Проверка: `tests/application/test_context_assembly.py` - все сценарии требования «Контекст PR входит в пакет как недоверенные данные» и сценарий «Длинное описание PR не вытесняет дифф»; `token_count` не больше лимита.
- [ ] 8.3 `app/application/use_cases/build_review_context.py`: получение контекста PR best-effort.
      Проверка: в `tests/application/test_context_assembly.py` или `test_review_pipeline.py` - `VcsError` на сообщениях коммитов: прогон продолжается, `omitted` содержит `pr_context` с `unavailable`.
- [ ] 8.4 `app/domain/prompt.py`: раздел «Контекст PR» в сообщении кода перед изменениями; пакет без `pull_request_context` даёт запрос без раздела.
      Проверка: `tests/domain/test_prompt.py` - точная раскладка раздела; тексты контекста PR отсутствуют в системном сообщении; старый пакет без поля собирается без ошибки и без раздела.
- [ ] 8.5 Документы: описание пакета в `docs/SYSTEM_DESIGN.md` и `docs/BACKEND_ARCHITECTURE.md` - поле `pull_request_context` и его доля бюджета.
      Проверка: `uv run pytest tests/test_docs.py`; ревью текста в PR.

## 9. Few-shot примеры

- [ ] 9.1 `prompts/review/few-shot.md` по design D8 и строка в `REVIEW_SYSTEM_PARTS` (`app/infrastructure/prompts.py`); `prompts/review/README.md` - состав системного сообщения.
      Проверка: `tests/infrastructure/test_prompts.py` - настоящий `prompts/review/` загружается, тело few-shot стоит в конце системного сообщения ревью; каждый блок ` ```json ` из тела проходит `ReviewOutput.model_validate_json`; среди них есть замечание с `rule` без префикса атрибуции, замечание с `rule: null` и ответ с пустым `findings`.

## 10. Архитектурные документы

- [ ] 10.1 `docs/BACKEND_ARCHITECTURE.md`: строка `LlmGateway` в таблице портов - адаптеры Ollama, Eurorouter и составной; модель угроз по design D9; раздел конфигурации - переменные D7.
      `docs/SYSTEM_DESIGN.md`: провайдеры и порядок фоллбэка на схеме и в сквозном пути.
      Проверка: `uv run pytest tests/test_docs.py`; ревью текста в PR.

## 11. Сквозная проверка

- [ ] 11.1 `tests/application/test_prompt_injection.py`: инструкция в описании PR и в сообщении коммита стоит внутри блоков недоверенных данных сообщения кода и отсутствует в системном сообщении.
      Проверка: кейс зелёный; при выводе контекста PR без `fence` кейс падает (мутационная проверка).
- [ ] 11.2 `uv run ruff check . && uv run mypy . && uv run lint-imports && uv run pytest`; с `TEST_DATABASE_URL` и `TEST_RABBITMQ_URL` - без пропусков; `npx @fission-ai/openspec@1.14.2 validate --all --strict`.
- [ ] 11.3 Вживую: `docker compose --profile ollama up -d --build`, в контейнер Ollama подтянута небольшая модель; разовый вызов `request_review` через `Container.llm_gateway()` на фиксированном пакете возвращает проверенный `ReviewOutput`, `model` - имя локальной модели; с `LLM_HOSTED_FALLBACK=true`, рабочим ключом Eurorouter и остановленной Ollama тот же вызов возвращает ответ с моделью Eurorouter; с остановленной Ollama и выключенным фоллбэком - `LlmError`.

## Workflow follow-up

- Ветка `feat/llm-gateway` отведена от `feat/prompt-guard-reflection-pass`: PR стопкой с базой в неё, привязка к #64 - мутацией `addCloseIssueReferences` и проверкой `closingIssuesReferences`.
- Архивировать этот change после архивации `add-prompt-guard-and-reflection-pass`; синхронизировать спеки и заархивировать (`openspec archive`) в том же PR до мерджа.
