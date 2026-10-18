# Spec Delta

## ADDED Requirements

### Requirement: Находка отдаёт ссылку на правило, прогон - итог верификации

`GET /api/v1/reviews/{review_run_id}` SHALL отдавать у каждой находки поле `rule`: объект с именем правила (`name`), номером пункта (`check`) и текстом пункта (`check_text`) либо `null` для находки по стандартным критериям.
Ответ SHALL содержать у прогона итог верификации (`verification`, `null` до завершения анализа) и число отброшенных верификатором находок (`verifier_dropped_findings`).

#### Scenario: Находка по правилу

- **WHEN** запрошен прогон с находкой по пункту 2 правила `Error Handling Standards`
- **THEN** у находки `rule` содержит `name` `Error Handling Standards`, `check` 2 и текст пункта

#### Scenario: Находка по стандартным критериям

- **WHEN** запрошен прогон с находкой без ссылки на правило
- **THEN** у находки `rule` равен `null`

#### Scenario: Прогон с недоступной верификацией

- **WHEN** запрошен завершённый прогон, верификатор которого был недоступен
- **THEN** `verification` равен `unavailable`, а `verifier_dropped_findings` - 0
