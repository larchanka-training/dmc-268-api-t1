# Tech Lead — план работы на Sprint 2

## Цель роли

Главная задача Tech Lead в этом спринте — создать единый контракт, по которому независимо работают Frontend, Backend, Queue/Worker, Context Engine, LLM и QA.

Результат работы — не только документация. Контракт должен стать общей точкой истины для реализации и тестирования.

---

## 1. Зависимости команды

```text
                         TECH LEAD
                 contracts / pipeline / schemas
                              │
             ┌────────────────┼────────────────┐
             ↓                ↓                ↓
          OpenAPI         ReviewJob         Finding
             │            State Machine       Schema
             │                │                │
             ↓                ↓                ↓
Frontend ← Backend ←──── Queue/Worker ───→ LLM Gateway
             ↑                │                │
             │                ↓                ↓
             └──────── Context Engine         LLM
                              │
                              ↓
                          Finding[]
                              │
                              ↓
                             QA
```

### Dependency Matrix

| Роль | Зависит от Tech Lead | Tech Lead должен получить |
|---|---|---|
| Frontend Base/Auth | auth/review API | требования UI |
| Frontend Review UI | Finding, statuses, errors | поля для review screen |
| Backend Architecture | OpenAPI, state model | ограничения persistence/API |
| Context Engine | pipeline boundaries | формат context/diff |
| LLM/Agentic Engineer | Finding Schema | ограничения structured output |
| Queue Engineer | ReviewJob states, retry | возможности queue/retry |
| QA | OpenAPI + schemas | edge cases и test scenarios |
| DevOps | service boundaries | timeout/infrastructure limits |

---

## 2. Критический путь

```text
Requirements
     ↓
Draft contracts
     ↓
Finding Schema ─────→ LLM implementation
     ↓
ReviewJob Schema ───→ Queue implementation
     ↓
OpenAPI ────────────→ Backend + Frontend
     ↓
Integrated pipeline
     ↓
Contract/E2E tests
```

Tech Lead не должен ждать полной реализации сервисов. Контракты должны быть согласованы достаточно рано, чтобы команда могла работать параллельно.

---

## 3. День 1 — Alignment

Провести короткую встречу с владельцами:

- Frontend;
- Backend;
- Queue/Worker;
- Context Engine;
- LLM;
- QA;
- при необходимости DevOps.

### Frontend

Уточнить:

- какие поля нужны Review Screen;
- как отображается progress;
- нужен ли polling;
- какие error states нужны;
- как UI показывает Findings;
- нужен ли диапазон строк;
- нужен ли suggested diff.

### Backend

Уточнить:

- persistence ReviewJob;
- создание review;
- получение status;
- хранение Findings;
- idempotency;
- pagination;
- error mapping.

### Queue/Worker

Уточнить:

- поддерживаемые retry;
- retry counter;
- visibility/ack semantics;
- duplicate delivery;
- timeout;
- dead-letter/failed jobs;
- state persistence.

### Context Engine

Уточнить:

- входные данные;
- результат parsing;
- ограничения context size;
- changed files;
- line mapping;
- обработку binary/large files.

### LLM Engineer

Уточнить:

- structured output;
- JSON Schema support;
- provider abstraction;
- timeout;
- invalid JSON;
- schema validation;
- repair strategy;
- token/context limits.

### QA

Уточнить:

- какие состояния должны тестироваться;
- негативные сценарии;
- contract validation;
- retry cases;
- malformed LLM output.

---

## 4. День 2 — Draft v0.1

Подготовить:

```text
docs/
├── pipeline/
│   ├── PIPELINE_SPEC.md
│   ├── review-job.schema.json
│   ├── finding.schema.json
│   └── error.schema.json
└── api/
    └── openapi.yaml
```

Создать PR:

`docs: define review pipeline and API contracts`

---

## 5. Contract Review

Не просить всех просто «посмотреть PR».

Назначить зоны ответственности:

### Frontend Reviewer
Проверяет:

- API response;
- ReviewJob;
- Finding;
- error states.

### Backend Reviewer
Проверяет:

- endpoints;
- persistence;
- HTTP semantics;
- state transitions.

### Queue Reviewer
Проверяет:

- state machine;
- retry;
- timeout;
- idempotency.

### LLM Reviewer
Проверяет:

- Finding Schema;
- structured output;
- validation;
- degradation.

### QA Reviewer
Проверяет:

- детерминированность;
- негативные сценарии;
- возможность contract tests.

---

## 6. День 3 — Contract v1

После review:

1. собрать unresolved comments;
2. провести короткий decision meeting только по спорным вопросам;
3. зафиксировать решения;
4. обновить схемы;
5. получить approvals;
6. объявить Contract v1.

После Contract v1 изменения публичного контракта должны проходить через отдельный review.

---

## 7. Что контролировать весь спринт

Tech Lead проверяет:

- contract drift;
- разные названия одного поля;
- новые undocumented states;
- undocumented endpoints;
- provider-specific поля в public API;
- разные error formats;
- дублирование моделей;
- silent retry;
- retry без idempotency;
- сохранение невалидного LLM output.

---

## 8. Чек-лист перед merge

- [ ] Все ReviewJob states описаны.
- [ ] Все state transitions определены.
- [ ] Terminal states определены.
- [ ] Finding Schema строгая.
- [ ] `additionalProperties: false`.
- [ ] Severity — enum.
- [ ] Category — enum.
- [ ] Error code — machine-readable.
- [ ] Retryable ошибки определены.
- [ ] Non-retryable ошибки определены.
- [ ] Timeout strategy согласована.
- [ ] Idempotency согласована.
- [ ] OpenAPI соответствует JSON Schemas.
- [ ] Frontend подтвердил контракт.
- [ ] Backend подтвердил контракт.
- [ ] Queue подтвердил контракт.
- [ ] LLM подтвердил контракт.
- [ ] QA подтвердил контракт.
