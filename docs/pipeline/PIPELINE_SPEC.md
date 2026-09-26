# Спецификация пайплайна ревью PR

**Статус:** Draft v0.1  
**Владелец:** Tech Lead  
**Спринт:** Sprint 2

## 1. Цель

Документ фиксирует межсервисные контракты и жизненный цикл автоматического ревью Pull Request.

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

`ReviewJob` — центральная сущность процесса ревью.

### State Machine

```text
QUEUED
   ↓
FETCHING_DIFF
   ↓
PARSING_CONTEXT
   ↓
LLM_PROCESSING
   ↓
COMPLETED

Любой processing-state
   ↓
retry, если ошибка временная
   ↓
FAILED, если retry исчерпан или ошибка невосстановимая
```

### Разрешённые переходы

| Текущее состояние | Следующее состояние |
|---|---|
| `QUEUED` | `FETCHING_DIFF`, `FAILED` |
| `FETCHING_DIFF` | `PARSING_CONTEXT`, `FAILED` |
| `PARSING_CONTEXT` | `LLM_PROCESSING`, `FAILED` |
| `LLM_PROCESSING` | `COMPLETED`, `FAILED` |
| `COMPLETED` | terminal |
| `FAILED` | terminal |

Worker не должен пропускать состояния. Изменение состояния должно сохраняться, чтобы Backend мог отдать актуальный статус Frontend.

---

## 3. Этапы обработки

### QUEUED

Backend:

- валидирует запрос;
- создаёт `ReviewJob`;
- сохраняет его;
- отправляет `reviewJobId` в очередь.

### FETCHING_DIFF

Worker получает:

- repository;
- Pull Request;
- commit SHA;
- changed files;
- diff.

### PARSING_CONTEXT

Context Engine:

- разбирает изменённые файлы;
- собирает необходимый контекст;
- ограничивает размер контекста;
- формирует нормализованный input для LLM.

### LLM_PROCESSING

LLM Gateway:

- принимает provider-independent input;
- вызывает LLM;
- получает structured output;
- валидирует результат;
- преобразует результат в `Finding[]`.

### COMPLETED

После успешной валидации:

- Findings сохраняются;
- ReviewJob получает `COMPLETED`;
- результат становится доступен через Backend API.

### FAILED

При невосстановимой ошибке или исчерпании retry:

- ReviewJob получает `FAILED`;
- сохраняется структурированная ошибка;
- Frontend получает безопасный error model.

---

## 4. Idempotency

Повторная доставка сообщения из очереди или retry не должны создавать дубли Findings.

Рекомендуемый logical key:

```text
repository + pullRequestNumber + headCommitSha
```

Повторная попытка обработки существующего `ReviewJob` должна использовать тот же `reviewJobId`.

Перед Contract v1 команда должна отдельно решить:

- допускается ли ручной повтор review для того же commit;
- нужен ли HTTP `Idempotency-Key`;
- как отличать retry существующей задачи от нового review.

---

## 5. Контракт LLM Output

LLM не имеет права определять публичную модель данных.

Каждый Finding валидируется по `finding.schema.json`.

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
          └── invalid → FAILED
```

Ошибка после исчерпания допустимой попытки восстановления:

`LLM_INVALID_OUTPUT`.

Невалидные Findings нельзя сохранять как валидный результат review.

---

## 6. Retry policy

Начальная политика для согласования:

| Ошибка | Retry | Поведение |
|---|---|---|
| LLM `429` | Да | exponential backoff + jitter |
| LLM `5xx` | Да | exponential backoff + jitter |
| Network timeout | Да | exponential backoff + jitter |
| Invalid JSON от LLM | Ограниченно | 1 repair/regeneration |
| JSON Schema validation error | Ограниченно | 1 repair/regeneration |
| GitHub transient `5xx` | Да | bounded retry |
| GitHub `401/403` | Нет | FAILED |
| PR not found | Нет | FAILED |
| Repository not found | Нет | FAILED |
| Invalid API request | Нет | reject до очереди |

Начальное предложение:

- максимум 3 provider attempts;
- timeout на каждый LLM request;
- exponential backoff;
- jitter;
- retry-параметры конфигурируемые.

Конкретные timeout/backoff значения должны быть согласованы с Queue, LLM и DevOps инженерами.

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
retry           FAILED
 ↓
success?
 ┌──────┴──────┐
yes            no
 ↓              ↓
continue      FAILED
```

После исчерпания retry job переводится в `FAILED`.

LLM Gateway должен позволять в будущем заменить провайдера без изменения публичного Backend/Frontend API.

---

## 8. Error Model

Ошибка должна содержать стабильный machine-readable `code`.

Пример:

```json
{
  "code": "LLM_TIMEOUT",
  "message": "LLM provider did not respond within the configured timeout",
  "retryable": true,
  "attempt": 3
}
```

Начальный набор кодов:

- `GITHUB_AUTH_ERROR`
- `PR_NOT_FOUND`
- `REPOSITORY_NOT_FOUND`
- `GITHUB_UNAVAILABLE`
- `CONTEXT_PARSING_ERROR`
- `LLM_RATE_LIMITED`
- `LLM_TIMEOUT`
- `LLM_UNAVAILABLE`
- `LLM_INVALID_OUTPUT`
- `INTERNAL_ERROR`

---

## 9. API

MVP endpoints:

```text
POST /reviews
GET  /reviews/{reviewId}
GET  /reviews/{reviewId}/findings
GET  /reviews
```

Источник истины для HTTP-контракта: `openapi.yaml`.

---

## 10. Наблюдаемость

Для каждого review необходимо иметь минимум:

- `reviewJobId`;
- repository;
- PR number;
- current state;
- timestamps;
- attempt;
- error code;
- LLM/provider request duration;
- correlation/request ID.

Нельзя логировать secrets, access tokens и другой чувствительный payload.

---

## 11. Definition of Done

Контракт считается готовым, когда:

- `PIPELINE_SPEC.md` согласован;
- `openapi.yaml` согласован;
- `review-job.schema.json` согласован;
- `finding.schema.json` согласован;
- `error.schema.json` согласован;
- определены retryable/non-retryable ошибки;
- определена стратегия timeout;
- определена деградация при LLM outage;
- Frontend подтвердил достаточность API;
- Backend подтвердил реализуемость API;
- Queue Engineer подтвердил state/retry model;
- LLM Engineer подтвердил structured output;
- QA подтвердил тестируемость переходов и ошибок;
- изменения прошли общий Contract Review.
