## RENAMED Requirements

- FROM: `### Requirement: Прогон без денег не учитывается в исходах прогонов`
- TO: `### Requirement: Отказ по балансу как отдельный исход прогонов`

## MODIFIED Requirements

### Requirement: Отказ по балансу как отдельный исход прогонов

Прогон, завершённый без денег, SHALL учитываться в `review_runs_total` исходом
`status="refused"`, ряд SHALL заводиться нулём при старте процесса. Доля упавших в
`ReviewRunsFailing` и её порог числа прогонов SHALL считаться только по `completed` и `failed`.
Алерт `ReviewRunsRefused` SHALL гореть, когда за час отказы составляют больше половины исходов
и их хотя бы три.

#### Scenario: Отказ и метрика

- **WHEN** прогон завершён без денег
- **THEN** `review_runs_total` растёт по `refused`, а по `completed` и `failed` не растёт

#### Scenario: Отказы не разбавляют долю упавших

- **WHEN** за 30 минут три прогона упали, один завершён и десять отказаны
- **THEN** горит `ReviewRunsFailing`: доля упавших 75 %

#### Scenario: Отказывают все прогоны

- **WHEN** за час все прогоны завершены без денег, и их хотя бы три
- **THEN** горит `ReviewRunsRefused`, а `ReviewRunsFailing` молчит
