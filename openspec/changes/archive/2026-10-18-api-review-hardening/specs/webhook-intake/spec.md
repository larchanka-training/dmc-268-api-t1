## ADDED Requirements

### Requirement: Тело 202 приёма вебхука — модель схемы

Ответ `202` приёма вебхука SHALL быть одной из моделей схемы: `ReviewJob` со всеми полями — поле без значения приходит как `null`, время — в UTC с `Z`, — `{"status": "ignored"}` или `{"status": "acknowledged"}` для `pull_request_review_thread`.

#### Scenario: Новый прогон

- **WHEN** подписанный `pull_request` `opened` создал прогон
- **THEN** тело `202` проходит модель `ReviewJob`, `model`, `tokensUsed`, `durationSeconds`, `failedStage` и `error` — `null`, `createdAt` оканчивается на `Z`
