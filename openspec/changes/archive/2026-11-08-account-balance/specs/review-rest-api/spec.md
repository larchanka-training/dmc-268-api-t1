# Spec Delta

## ADDED Requirements

### Requirement: Плательщик в списке репозиториев

Элемент `GET /api/v1/repositories` SHALL отдавать `payer_login`: логин GitHub плательщика
подключённого репозитория или `null`, если плательщика нет или репозиторий не подключён. Ответ
`POST /api/v1/repositories/{repository_id}/payer` SHALL отдавать то же поле.

#### Scenario: Репозиторий с плательщиком

- **WHEN** у подключённого репозитория плательщик `alice`
- **THEN** его элемент списка содержит `payer_login = "alice"`

#### Scenario: Репозиторий без плательщика

- **WHEN** репозиторий подключён до биллинга, и плательщик не назначен
- **THEN** его элемент списка содержит `payer_login = null`

### Requirement: Причина покрытия прогона

`GET /api/v1/reviews/{review_run_id}`, строка `ReviewRunListItem` списка `GET /api/v1/reviews` и
данные событий `run.*` потока `GET /api/v1/events` SHALL отдавать `coverage_reason`: `"balance"`,
если прогон завершён без вызова модели из-за нехватки денег, иначе `null`.

#### Scenario: Прогон без денег

- **WHEN** прогон завершён без вызова модели, потому что баланса плательщика не хватило
- **THEN** `status = "completed"`, `coverage_reason = "balance"`, находок нет

#### Scenario: Обычный прогон

- **WHEN** прогон завершён с вызовом модели
- **THEN** `coverage_reason = null`

#### Scenario: Прогон без денег в списке

- **WHEN** вызывается `GET /api/v1/reviews`, и среди прогонов есть завершённый без денег
- **THEN** его строка содержит `coverage_reason = "balance"`, и клиенту не нужно открывать прогон, чтобы отличить его от чистого ревью

## MODIFIED Requirements

### Requirement: Прогон отдаёт разбивку токенов и стоимость

`GET /api/v1/reviews/{review_run_id}` SHALL отдавать поле `usage`: объект с `input_tokens`, `output_tokens` и стоимостью `cost_eur` — точной десятичной строкой в евро без экспоненты и хвостовых нулей (ноль — `"0"`), — либо `null`, если прогон разбивки не накопил (до анализа или записан до её появления). Прогон биллинга, завершённый без вызова модели (без денег или с резервом, снятым до анализа), SHALL отдавать нулевой `usage`, а не `null`.

#### Scenario: Прогон с расходом

- **WHEN** вызывается `GET /api/v1/reviews/{review_run_id}` для прогона с 30 входными, 5 выходными токенами и стоимостью 0,0000913 €
- **THEN** `usage` — `{"input_tokens": 30, "output_tokens": 5, "cost_eur": "0.0000913"}`

#### Scenario: Прогон без расхода

- **WHEN** вызывается `GET /api/v1/reviews/{review_run_id}` для прогона в `queued`
- **THEN** `usage` равен `null`

#### Scenario: Прогон без денег

- **WHEN** вызывается `GET /api/v1/reviews/{review_run_id}` для прогона, завершённого без вызова модели из-за нехватки денег
- **THEN** `usage` — `{"input_tokens": 0, "output_tokens": 0, "cost_eur": "0"}`, `tokens_used` равен 0
