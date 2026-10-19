# Спецификация пайплайна ревью PR

**Статус:** Draft v0.1  
**Владелец:** Tech Lead  
**Спринт:** Sprint 2

## 1. Цель

Документ фиксирует межсервисные контракты и жизненный цикл автоматического ревью Pull Request.

Словарь состояний, категорий и сущностей документ **наследует** из
[`review-data-model`](../../openspec/specs/review-data-model/spec.md) и
[`ai-review-prompting`](../../openspec/specs/ai-review-prompting/spec.md) — он их не
переопределяет. Любое поведение, которого в этих спеках ещё нет (на сегодня —
персистентность стабильного `ReviewError.code`, см. §8, и аутентификация пользователя,
см. §9.1), фиксируется здесь как предложение и до Contract v1 должно пройти отдельный
OpenSpec-change. Лимит устаревания прогона (§7.5) и публикация комментариев такого
change уже не требуют — оба входят в смерженный `review-data-model` («Прогон не может
навсегда заблокировать свой коммит», «Опубликованные комментарии отслеживаются»).

Основной поток:

```text
Frontend
   ↓
Backend API
   ↓
Task Queue
   ↓
Review Worker
   ├── GitHub / SCM
   ├── Context Engine
   └── LLM Gateway
            ↓
           LLM
            ↓
        Finding[]
            ↓
         Backend
            ↓
        Frontend
```

### Архитектурные правила

1. Frontend общается только с Backend API.
2. Frontend не должен напрямую обращаться к Worker или LLM.
3. Backend создаёт `ReviewJob` и ставит задачу в очередь.
4. Worker выполняет длительную обработку.
5. LLM Gateway изолирует систему от конкретного LLM-провайдера.
6. Ответ LLM считается недоверенными данными и обязательно валидируется.
7. Публичные API-контракты не должны зависеть от формата конкретного LLM.

---

## 2. ReviewJob

`ReviewJob` — публичная проекция хранимой сущности `ReviewRun` (см. `review-data-model`).
Это не то же самое, что сообщение в очереди: сообщение несёт `review_run_id`, у него нет
собственной идентичности, отдельной от прогона, который оно продвигает.

### State Machine

Состояния и переходы — те же, что в `ReviewRunStatus` и `app/domain/lifecycle.py`. Таблица
переходов ниже — вручную поддерживаемое зеркало `_ALLOWED`; расхождение ловит
`tests/test_docs.py::test_pipeline_spec_transition_table_matches_lifecycle`, а не читатель.

```text
queued
   ↓
building_context
   ↓
analysing
   ↓
publishing
   ↓
completed

Любое нетерминальное состояние
   ↓
cancelled — по явному запросу (см. §3, cancelled)
   ↓
failed — если ошибка невосстановима, retry исчерпан или прогон признан устаревшим (§7.5)
```

### Разрешённые переходы

| Текущее состояние | Следующее состояние |
|---|---|
| `queued` | `building_context`, `failed`, `cancelled` |
| `building_context` | `analysing`, `failed`, `cancelled` |
| `analysing` | `publishing`, `failed`, `cancelled` |
| `publishing` | `completed`, `failed`, `cancelled` |
| `completed` | terminal |
| `failed` | terminal |
| `cancelled` | terminal |

Worker не должен пропускать состояния. Изменение состояния должно сохраняться, чтобы Backend мог отдать актуальный статус Frontend.

---

## 3. Этапы обработки

### queued

Backend:

- валидирует запрос, включая `provider` + `provider_repository_id` и `trigger`;
- создаёт `ReviewRun` в состоянии `queued`;
- сохраняет его;
- отправляет `review_run_id` в очередь (RabbitMQ, см. §6).

### building_context

Worker и Context Engine в рамках одного состояния:

- получают repository, Pull Request, commit SHA, changed files, diff;
- разбирают изменённые файлы;
- собирают необходимый контекст;
- ограничивают размер контекста;
- формируют нормализованный input для LLM.

Отдельного состояния для «только получить diff» нет — фиксируется прогресс внутри
`building_context`, а не два разных статуса.

### analysing

LLM Gateway:

- принимает provider-independent input;
- вызывает LLM;
- получает structured output;
- валидирует результат;
- преобразует результат в `Finding[]`.

### publishing

Backend/Worker публикует результат на хостинг (см. `review-data-model`,
«Опубликованные комментарии отслеживаются»):

- инлайн-комментарий на каждый сохранённый `Finding`, не более одного раза за прогон;
- один итоговый (`summary`) комментарий на прогон, не более одного раза;
- каждая публикация фиксируется записью `PublishedComment` с `providerCommentId`.

### completed

После успешной публикации:

- Findings сохранены;
- ReviewRun получает `completed`, фиксируются `model`, `tokens_used`, `duration_seconds`;
- результат доступен через Backend API.

### failed

При невосстановимой ошибке, исчерпании retry или признании прогона устаревшим (§7.5):

- ReviewRun получает `failed`, фиксируется `failureReason`;
- сохраняется структурированная ошибка;
- Frontend получает безопасный error model.

### cancelled

По явному запросу (например, ручная отмена или PR закрыт/смёржен до завершения ревью):

- ReviewRun получает `cancelled` из любого нетерминального состояния;
- Findings, накопленные до отмены, не публикуются;
- повторная доставка задачи из очереди для уже `cancelled` прогона игнорируется.

---

## 4. Idempotency

Повторная доставка сообщения из очереди или retry не должны создавать дубли Findings.

Рекомендуемый logical key:

```text
provider + provider_repository_id + pull_request_number + head_commit_sha
```

Повторная попытка обработки существующего `ReviewRun` должна использовать тот же `review_run_id`.
Не более одного нетерминального прогона на этот ключ уже гарантирует БД
(`uq_review_runs_one_active_per_commit`) — API не создаёт второй `ReviewRun`, а отвечает
по-разному в зависимости от того, кто прислал дубль:

- `POST /reviews` (ручной запуск) — `409` с `code = REVIEW_ALREADY_ACTIVE`:
  человек должен увидеть, что прогон уже идёт;
- `POST /webhooks/{provider}` — `202` с уже существующим `ReviewJob`: повторная доставка
  вебхука штатна, а не-2xx хостинг считает неудачей и повторяет доставку снова.

Перед Contract v1 команда должна отдельно решить:

- допускается ли ручной повтор review для того же commit;
- нужен ли HTTP `Idempotency-Key`;
- как отличать retry существующей задачи от нового review.

---

## 5. Контракт LLM Output

LLM не имеет права определять публичную модель данных.

Каждый Finding в ответе LLM валидируется по `docs/openapi/finding.schema.json` — схеме вывода
LLM (на неё ссылается `docs/openapi/llm_review_output.schema.json`), а не схеме API: находку
в API описывает `FindingResponse` в схеме из кода (`docs/openapi/openapi.json`).

При invalid output:

```text
LLM response
   ↓
JSON parse
   ↓
JSON Schema validation
   ├── valid → persist
   └── invalid
          ↓
     repair/regeneration
          ↓
       validate
          ├── valid → persist
          └── invalid → failed
```

Ошибка после исчерпания допустимой попытки восстановления:

`LLM_INVALID_OUTPUT`.

Невалидные Findings нельзя сохранять как валидный результат review.

---

## 6. Retry policy

Брокер очереди — **RabbitMQ** (`docs/BACKEND_ARCHITECTURE.md`, порт `JobQueue`). Redis в
пайплайне отвечает только за кеш и идемпотентность HTTP-слоя и к retry/DLQ отношения не имеет.

### Три независимых счётчика попыток

Документ ранее смешивал в одном `attempt` три разных вещи. Разводим их:

- **`provider_attempt`** — попытка вызвать внешнего провайдера (LLM или SCM) внутри одного
  прохождения состояния. Инкрементируется клиентом провайдера, ограничена лимитом из этого
  раздела, попадает в публичную `ReviewError` (§8).
- **redelivery сообщения** — брокер повторно доставляет то же сообщение очереди после
  visibility timeout. Это внутренняя метрика RabbitMQ, в публичный контракт не выходит,
  но фиксируется в наблюдаемости (§10) как `queueRedeliveryCount`.
- **попытка задачи целиком** — не заводим отдельным счётчиком. Задача либо продвигается в
  рамках текущего `ReviewRun`, либо тот переходит в `failed`; повторный прогон того же
  коммита — это новый `ReviewRun` с собственным `review_run_id` (см. §4).

### Visibility timeout и dead-letter

- **Visibility timeout** — время, в течение которого RabbitMQ ждёт `ack` по сообщению;
  реализуется через настройку consumer'а и обрыв соединения при его превышении, а не
  отдельной pending-записью, как это устроено у Redis-очередей.
- **Dead-letter** — сообщение, исчерпавшее `provider_attempt` или превысившее visibility
  timeout сверх допустимого числа redelivery, уходит в dead-letter exchange с отдельной
  политикой обработки (минимум — алертинг), а не удаляется молча.

### Retryable / non-retryable ошибки

| Ошибка | Retry | Поведение |
|---|---|---|
| LLM `429` | Да | exponential backoff + jitter, инкремент `provider_attempt` |
| LLM `5xx` | Да | exponential backoff + jitter, инкремент `provider_attempt` |
| Network timeout | Да | exponential backoff + jitter, инкремент `provider_attempt` |
| Invalid JSON от LLM | Ограниченно | 1 repair/regeneration |
| JSON Schema validation error | Ограниченно | 1 repair/regeneration |
| SCM transient `5xx` | Да | bounded retry, инкремент `provider_attempt` |
| SCM `401/403` | Нет | `failed` |
| PR not found | Нет | `failed` |
| Repository not found | Нет | `failed` |
| Invalid API request | Нет | reject до очереди |

Начальное предложение:

- максимум 3 `provider_attempt` на вызов LLM/SCM;
- timeout на каждый LLM/SCM request;
- exponential backoff;
- jitter;
- retry-параметры конфигурируемые.

Конкретные timeout/backoff значения и лимит redelivery в RabbitMQ должны быть согласованы с
Queue, LLM и DevOps инженерами.

---

## 7. Деградация при сбое LLM

Сбой LLM не должен делать Backend API недоступным.

Если LLM временно недоступен:

```text
LLM failure
   ↓
retryable?
 ┌───────┴───────┐
yes              no
 ↓                ↓
retry           failed
 ↓
success?
 ┌──────┴──────┐
yes            no
 ↓              ↓
continue      failed
```

После исчерпания retry job переводится в `failed`.

LLM Gateway должен позволять в будущем заменить провайдера без изменения публичного Backend/Frontend API.

---

## 7.5. Зависшие прогоны

Поскольку на пару (`ReviewRun.merge_request_id`, `head_sha`) допустим только один
нетерминальный прогон (`uq_review_runs_one_active_per_commit`), воркер, упавший между
состояниями, иначе навсегда блокирует коммит — новый ревью для него создать нельзя, пока
старый висит.

- Каждый прогон фиксирует `last_progress_at` (обновляется при каждом переходе состояния).
- Прогон, не продвигавшийся дольше настроенного лимита устаревания, переводится в `failed`
  фоновым сборщиком с кодом ошибки `REVIEW_RUN_STALE` (§8).
- Перевод в `failed` освобождает коммит: новый `ReviewRun` для того же
  (`merge_request_id`, `head_sha`) можно создать сразу после этого.
- `last_progress_at` отдаётся в `ReviewJob` (§9), чтобы Frontend мог показать зависшую задачу
  до того, как её подберёт сборщик.

Конкретное значение лимита устаревания — предмет согласования с Queue/DevOps.

---

## 8. Error Model

Ошибка должна содержать стабильный machine-readable `code`.

Коды ошибок SCM — провайдер-нейтральные: `Provider` уже сегодня включает `github` и
`gitlab` (`app/domain/enums.py`), значит поддержка второго провайдера — не гипотетическое
будущее, а заявленный домен. Публичный enum ошибок не должен требовать breaking change
при подключении второго провайдера, поэтому вместо `GITHUB_*` — `SCM_*` с полем
`scm_provider`, а не просто `provider`: `provider_attempt` уже считает попытки и LLM, и SCM,
и поле с именем `provider`, типизированное как `Provider` (`github`/`gitlab`), для
retryable LLM-ошибок (почти вся таблица §6) осталось бы пустым, хотя формально называет
отказавшую сторону.

Пример:

```json
{
  "code": "LLM_TIMEOUT",
  "message": "LLM provider did not respond within the configured timeout",
  "retryable": true,
  "provider_attempt": 3,
  "scm_provider": null
}
```

Начальный набор кодов:

- `SCM_AUTH_ERROR` (поле `scm_provider` заполнено)
- `PR_NOT_FOUND`
- `REPOSITORY_NOT_FOUND`
- `SCM_UNAVAILABLE` (поле `scm_provider` заполнено)
- `CONTEXT_PARSING_ERROR`
- `LLM_RATE_LIMITED`
- `LLM_TIMEOUT`
- `LLM_UNAVAILABLE`
- `LLM_INVALID_OUTPUT`
- `REVIEW_RUN_STALE` (§7.5)
- `INTERNAL_ERROR`

### Персистентность `code`

`review-data-model` («Сбой фиксирует причину») даёт прогону только `failure_reason` (текст)
и время — колонки под стабильный machine-readable `code` в схеме нет. Ответ API может
построить `code` в момент сбоя (значение известно вызывающему коду), но прочитать его
из уже сохранённого `failed`-прогона задним числом сегодня нельзя. Это и есть то
поведение вне действующих спек, которое требует отдельного OpenSpec-change к
`review-data-model` до Contract v1 (см. §1, §11).

---

## 9. API

MVP endpoints:

```text
GET  /repositories
GET  /repositories/{repository_id}/pull-requests
GET  /reviews/{review_run_id}
GET  /reviews/{review_run_id}/events
POST /webhooks/{provider}
POST /reviews                    # заглушка: схема описана, отвечает 501 NOT_IMPLEMENTED
GET  /auth/{provider}/login
GET  /auth/{provider}/callback
GET  /auth/me
POST /auth/logout
```

Находки прогона приходят в ответе `GET /reviews/{review_run_id}`, отдельного
`/reviews/{review_run_id}/findings` нет (спека `review-rest-api`). Списка прогонов `GET /reviews`
тоже нет. Вход и сессия (`/auth/...`) — §9.1.

`POST /webhooks/{provider}` принимает доставки от хостинга (см.
`docs/BACKEND_ARCHITECTURE.md`, «Подлинность webhook'ов»):

- подпись проверяется алгоритмом и заголовком, специфичным для провайдера; секрет для
  проверки не хранится в репозитории и не логируется;
- запрос без валидной подписи отклоняется до постановки в очередь;
- успешный вебхук создаёт `ReviewRun` с `trigger = webhook`, используя тот же logical key
  идемпотентности, что и ручной запуск (§4);
- доставка, которая ревью не запускает (действие `reopened`, `closed` или неизвестное,
  незарегистрированный репозиторий), — `202` с телом `WebhookIgnored` (`{"status": "ignored"}`):
  записей нет, очередь не тронута;
- сбой VCS при получении диффа или метаданных — `502` с `code = SCM_UNAVAILABLE`,
  прогон не создаётся; провайдер из `Provider`, для которого приёмника ещё нет, — `501` с
  `PROVIDER_NOT_SUPPORTED`.

`CreateReviewRequest` идентифицирует репозиторий парой `provider` + `provider_repository_id`
(соответствует `UNIQUE (provider, provider_id)` в таблице `repositories`), а не строкой вида
`owner/repository` — `full_name` в этой таблице намеренно не уникален. Обязателен `trigger`.

Все пути — под `/api/v1` (пути в схеме уже с префиксом): префикс ставит само
приложение, а не reverse-proxy, поэтому фронт ходит на `/api/v1/reviews`, а хостинг шлёт
вебхуки на `/api/v1/webhooks/{provider}`.

Ошибки HTTP-слоя — RFC 9457 Problem Details, тип содержимого `application/problem+json`:

```json
{
  "type": "urn:dmc-268:problem:validation-error",
  "title": "Запрос не прошёл проверку",
  "status": 422,
  "detail": "Параметры запроса не прошли проверку, ошибок: 1.",
  "instance": "/api/v1/repositories",
  "code": "VALIDATION_ERROR",
  "trace_id": "4bf92f3577b34da6a3ce929d0e0e4736",
  "errors": [{"where": "query.limit", "reason": "ожидается целое число"}]
}
```

- `type` — `urn:dmc-268:problem:<код в kebab-case>`, `title` — короткое описание по-русски;
  оба постоянны для кода. `detail` — что случилось в этом запросе, без значений из запроса.
  `instance` — путь запроса без query в том виде, в каком он пришёл, — закодированный
  (`/api/v1/a%20b`), то есть URI-reference; запрет повторять переданное значение относится
  к `detail` и `errors`.
- `code` — расширение с закрытым списком `ErrorCode`: клиент ветвится по нему, а не по
  тексту. Коды: `VALIDATION_ERROR`, `REVIEW_NOT_FOUND`, `REPOSITORY_NOT_FOUND`,
  `REVIEW_ALREADY_ACTIVE`, `WEBHOOK_SIGNATURE_INVALID`, `SCM_UNAVAILABLE`,
  `PROVIDER_NOT_SUPPORTED`, `UNAUTHENTICATED`, `ORIGIN_FORBIDDEN`, `ROUTE_NOT_FOUND` (`404`
  неизвестного пути), `METHOD_NOT_ALLOWED` (`405`, разрешённые методы — в `Allow`),
  `NOT_IMPLEMENTED` (`501` запланированной ручки, описанной в схеме заранее),
  `HTTP_ERROR` (прочий `HTTPException` фреймворка или зависимости — статус и заголовки
  исключения, например `429` с `Retry-After`), `INTERNAL_ERROR` (`500`, без деталей исключения). Модель
  на каждый код — в `app/api/problems.py`, в схеме — объединение `AnyProblem` с
  discriminator по `code`; у каждой операции в схеме — её коды и `default` с `AnyProblem`.
- `trace_id` — 32 строчных hex-символа (формат trace-id W3C Trace Context, не все нули),
  который сервер выдаёт каждому запросу сам и отдаёт заголовком `X-Trace-ID` на любом
  ответе, не только на ошибке. Входящие `X-Trace-ID`, `X-Request-ID` и `traceparent` не
  принимаются. На `500` сервер пишет одну строку лога уровня error с `trace_id`, методом и
  путём — по `trace_id` из сообщения пользователя её и ищут; стектрейс пишет uvicorn.
  Когда подключим OpenTelemetry, значением станет trace id OTel — формат тот же.
- Дополнительные данные есть только у `VALIDATION_ERROR`: `errors` — `where` (часть запроса
  и имя параметра) и `reason` (причина по-русски).

`404` ручек — код по сущности, ошибка проверки параметров — `422` с `VALIDATION_ERROR`.
Ошибки прогона (`ReviewError`) — другой контракт: это поле `ReviewJob`, а не тело
HTTP-ошибки.

Источник истины для HTTP-контракта — код: роуты и модели `app/api`, из которых FastAPI
строит схему (`/openapi.json`). `docs/openapi/openapi.json` — её экспорт
(`uv run python scripts/export_openapi.py`), руками не правится; CI проверяет, что он свежий.

### 9.1. Аутентификация

Вход через GitHub выполняет бэкенд, сессия живёт в httpOnly cookie и хранится на
сервере. Требования — спека `user-auth` (`openspec/specs/user-auth/spec.md`), ручки —
`docs/openapi/openapi.json`, обоснование выбора — `openspec/changes/archive/2026-10-17-auth-session/design.md`.

---

## 10. Наблюдаемость

Для каждого review необходимо иметь минимум:

- `review_run_id`;
- provider + provider_repository_id;
- PR number;
- current state;
- timestamps, включая `last_progress_at`;
- `provider_attempt`;
- `queueRedeliveryCount`;
- error code;
- LLM/provider request duration;
- correlation/request ID.

Нельзя логировать secrets, access tokens и другой чувствительный payload.

---

## 11. Definition of Done

Контракт считается готовым, когда:

- словарь состояний, категорий и сущностей совпадает с `review-data-model` и
  `ai-review-prompting` дословно, а не переопределён параллельно;
- `PIPELINE_SPEC.md` согласован;
- схема API (`docs/openapi/openapi.json`, экспорт из кода) согласована, `ReviewJob` и
  `ReviewError` — в ней;
- схема находки в выводе LLM (`docs/openapi/finding.schema.json`) согласована;
- определены retryable/non-retryable ошибки;
- определена стратегия timeout;
- определена деградация при LLM outage;
- определён лимит устаревания зависших прогонов (§7.5);
- Frontend подтвердил достаточность API;
- Backend подтвердил реализуемость API;
- Queue Engineer подтвердил state/retry model, включая RabbitMQ visibility timeout и dead-letter;
- LLM Engineer подтвердил structured output;
- QA подтвердил тестируемость переходов и ошибок;
- поведение, не покрытое действующими OpenSpec-спеками — персистентность `ReviewError.code`
  (§8) и аутентификация пользователя (§9.1) — оформлено отдельными OpenSpec-change; §7.5 и
  публикация комментариев уже входят в смерженный `review-data-model` и отдельного change
  не требуют;
- изменения прошли общий Contract Review.
