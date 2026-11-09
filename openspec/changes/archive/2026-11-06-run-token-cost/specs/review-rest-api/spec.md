## ADDED Requirements

### Requirement: Прогон отдаёт разбивку токенов и стоимость

`GET /api/v1/reviews/{review_run_id}` SHALL отдавать поле `usage`: объект с `input_tokens`, `output_tokens` и стоимостью `cost_eur` — точной десятичной строкой в евро без экспоненты и хвостовых нулей (ноль — `"0"`), — либо `null`, если прогон разбивки не накопил (до анализа или записан до её появления).

#### Scenario: Прогон с расходом

- **WHEN** вызывается `GET /api/v1/reviews/{review_run_id}` для прогона с 30 входными, 5 выходными токенами и стоимостью 0,0000913 €
- **THEN** `usage` — `{"input_tokens": 30, "output_tokens": 5, "cost_eur": "0.0000913"}`

#### Scenario: Прогон без расхода

- **WHEN** вызывается `GET /api/v1/reviews/{review_run_id}` для прогона в `queued`
- **THEN** `usage` равен `null`
