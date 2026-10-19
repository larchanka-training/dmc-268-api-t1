## MODIFIED Requirements

### Requirement: Тело 202 приёма вебхука — модель схемы

Ответ `202` приёма вебхука SHALL быть одной из моделей схемы: `ReviewJob` со всеми полями — поле без значения приходит как `null`, время — в UTC с `Z`, — `{"status": "ignored"}` или `{"status": "acknowledged"}` для `pull_request_review_thread`.

#### Scenario: Новый прогон

- **WHEN** подписанный `pull_request` `opened` создал прогон
- **THEN** тело `202` проходит модель `ReviewJob`, `model`, `tokens_used`, `duration_seconds`, `failed_stage` и `error` — `null`, `created_at` оканчивается на `Z`
