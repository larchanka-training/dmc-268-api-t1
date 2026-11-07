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

`GET /api/v1/reviews/{review_run_id}` SHALL отдавать `coverage_reason`: `"balance"`, если прогон
завершён без вызова модели из-за нехватки денег, иначе `null`.

#### Scenario: Прогон без денег

- **WHEN** прогон завершён без вызова модели, потому что баланса плательщика не хватило
- **THEN** `status = "completed"`, `coverage_reason = "balance"`, находок нет

#### Scenario: Обычный прогон

- **WHEN** прогон завершён с вызовом модели
- **THEN** `coverage_reason = null`
