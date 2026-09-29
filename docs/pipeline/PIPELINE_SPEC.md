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
Это не то же самое, что сообщение в очереди: сообщение несёт `reviewRunId`, у него нет
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

- валидирует запрос, включая `provider` + `providerRepositoryId` и `trigger`;
- создаёт `ReviewRun` в состоянии `queued`;
- сохраняет его;
- отправляет `reviewRunId` в очередь (RabbitMQ, см. §6).

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
- ReviewRun получает `completed`, фиксируются `model`, `tokensUsed`, `durationSeconds`;
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
provider + providerRepositoryId + pullRequestNumber + headCommitSha
```

Повторная попытка обработки существующего `ReviewRun` должна использовать тот же `reviewRunId`.
Не более одного нетерминального прогона на этот ключ уже гарантирует БД
(`uq_review_runs_one_active_per_commit`) — контракт API должен транслировать отказ на
уровне БД в `409`, а не создавать второй `ReviewRun`.

Перед Contract v1 команда должна отдельно решить:

- допускается ли ручной повтор review для того же commit;
- нужен ли HTTP `Idempotency-Key`;
- как отличать retry существующей задачи от нового review.

---

## 5. Контракт LLM Output

LLM не имеет права определять публичную модель данных.

Каждый Finding валидируется по `docs/openapi/finding.schema.json`.

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

- **`providerAttempt`** — попытка вызвать внешнего провайдера (LLM или SCM) внутри одного
  прохождения состояния. Инкрементируется клиентом провайдера, ограничена лимитом из этого
  раздела, попадает в публичную `ReviewError` (§8).
- **redelivery сообщения** — брокер повторно доставляет то же сообщение очереди после
  visibility timeout. Это внутренняя метрика RabbitMQ, в публичный контракт не выходит,
  но фиксируется в наблюдаемости (§10) как `queueRedeliveryCount`.
- **попытка задачи целиком** — не заводим отдельным счётчиком. Задача либо продвигается в
  рамках текущего `ReviewRun`, либо тот переходит в `failed`; повторный прогон того же
  коммита — это новый `ReviewRun` с собственным `reviewRunId` (см. §4).

### Visibility timeout и dead-letter

- **Visibility timeout** — время, в течение которого RabbitMQ ждёт `ack` по сообщению;
  реализуется через настройку consumer'а и обрыв соединения при его превышении, а не
  отдельной pending-записью, как это устроено у Redis-очередей.
- **Dead-letter** — сообщение, исчерпавшее `providerAttempt` или превысившее visibility
  timeout сверх допустимого числа redelivery, уходит в dead-letter exchange с отдельной
  политикой обработки (минимум — алертинг), а не удаляется молча.

### Retryable / non-retryable ошибки

| Ошибка | Retry | Поведение |
|---|---|---|
| LLM `429` | Да | exponential backoff + jitter, инкремент `providerAttempt` |
| LLM `5xx` | Да | exponential backoff + jitter, инкремент `providerAttempt` |
| Network timeout | Да | exponential backoff + jitter, инкремент `providerAttempt` |
| Invalid JSON от LLM | Ограниченно | 1 repair/regeneration |
| JSON Schema validation error | Ограниченно | 1 repair/regeneration |
| SCM transient `5xx` | Да | bounded retry, инкремент `providerAttempt` |
| SCM `401/403` | Нет | `failed` |
| PR not found | Нет | `failed` |
| Repository not found | Нет | `failed` |
| Invalid API request | Нет | reject до очереди |

Начальное предложение:

- максимум 3 `providerAttempt` на вызов LLM/SCM;
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
- `lastProgressAt` отдаётся в `ReviewJob` (§9), чтобы Frontend мог показать зависшую задачу
  до того, как её подберёт сборщик.

Конкретное значение лимита устаревания — предмет согласования с Queue/DevOps.

---

## 8. Error Model

Ошибка должна содержать стабильный machine-readable `code`.

Коды ошибок SCM — провайдер-нейтральные: `Provider` уже сегодня включает `github` и
`gitlab` (`app/domain/enums.py`), значит поддержка второго провайдера — не гипотетическое
будущее, а заявленный домен. Публичный enum ошибок не должен требовать breaking change
при подключении второго провайдера, поэтому вместо `GITHUB_*` — `SCM_*` с полем
`scmProvider`, а не просто `provider`: `providerAttempt` уже считает попытки и LLM, и SCM,
и поле с именем `provider`, типизированное как `Provider` (`github`/`gitlab`), для
retryable LLM-ошибок (почти вся таблица §6) осталось бы пустым, хотя формально называет
отказавшую сторону.

Пример:

```json
{
  "code": "LLM_TIMEOUT",
  "message": "LLM provider did not respond within the configured timeout",
  "retryable": true,
  "providerAttempt": 3,
  "scmProvider": null
}
```

Начальный набор кодов:

- `SCM_AUTH_ERROR` (поле `scmProvider` заполнено)
- `PR_NOT_FOUND`
- `REPOSITORY_NOT_FOUND`
- `SCM_UNAVAILABLE` (поле `scmProvider` заполнено)
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
POST /reviews
GET  /reviews/{reviewId}
GET  /reviews/{reviewId}/findings
GET  /reviews
POST /webhooks/{provider}
```

Аутентификация пользователя (`POST /auth/oauth/token`, `POST /auth/refresh`) сюда не
входит — proposal, см. §9.1.

`POST /webhooks/{provider}` принимает доставки от хостинга (см.
`docs/BACKEND_ARCHITECTURE.md`, «Подлинность webhook'ов»):

- подпись проверяется алгоритмом и заголовком, специфичным для провайдера; секрет для
  проверки не хранится в репозитории и не логируется;
- запрос без валидной подписи отклоняется до постановки в очередь;
- успешный вебхук создаёт `ReviewRun` с `trigger = webhook`, используя тот же logical key
  идемпотентности, что и ручной запуск (§4).

`CreateReviewRequest` идентифицирует репозиторий парой `provider` + `providerRepositoryId`
(соответствует `UNIQUE (provider, provider_id)` в таблице `repositories`), а не строкой вида
`owner/repository` — `full_name` в этой таблице намеренно не уникален. Обязателен `trigger`.

Источник истины для HTTP-контракта: `openapi.yaml`.

### 9.1. Аутентификация (proposal)

`POST /reviews` подразумевает пользователя: кто-то должен ходить в SCM его правами и иметь
право запускать ревью по конкретному репозиторию. Ни в одном из перечисленных выше
endpoint'ов, ни в текущих OpenSpec-спеках этого нет — аутентификация здесь фиксируется как
предложение и, как и `ReviewError.code` (§8), должна пройти отдельный OpenSpec-change до
Contract v1 (см. §1).

```text
POST /auth/oauth/token
POST /auth/refresh
```

- `POST /auth/oauth/token` обменивает код OAuth-редиректа провайдера (`provider` +
  `code`) на сессию.
- `POST /auth/refresh` обновляет access-токен по refresh-куке, без тела запроса.
- Обе ручки отдают access-токен в теле ответа (`AuthSession.accessToken`,
  `expiresIn` в секундах) и переиздают refresh-токен `Set-Cookie`-заголовком:
  `HttpOnly; Secure; SameSite=Strict`, `Path=/auth/refresh` — кука не читается
  скриптом и не уходит ни на один другой путь API.
- Транспорт сессии на Frontend: access-токен — в памяти вкладки, не в
  `localStorage`; при перезагрузке — тихий `POST /auth/refresh` по куке. Это
  решение бэкенда, а не фронта, потому что `Set-Cookie` ставит только сервер.
- Ошибки — `ApiError`: `AUTH_CODE_INVALID` (код OAuth недействителен/истёк),
  `AUTH_REFRESH_INVALID` (refresh-кука отсутствует, истекла или отозвана).

Единый origin у Frontend и API через reverse-proxy (что и делает `SameSite=Strict`
рабочим без CORS) в контракт не входит — это устройство инфраструктуры конкретного
деплоя, а не публичный API.

---

## 10. Наблюдаемость

Для каждого review необходимо иметь минимум:

- `reviewRunId`;
- provider + providerRepositoryId;
- PR number;
- current state;
- timestamps, включая `lastProgressAt`;
- `providerAttempt`;
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
- `openapi.yaml` согласован;
- `docs/openapi/review-job.schema.json` согласован;
- `docs/openapi/finding.schema.json` согласован;
- `docs/openapi/error.schema.json` согласован;
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
