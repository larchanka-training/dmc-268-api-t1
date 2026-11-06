## MODIFIED Requirements

### Requirement: Подпуть Grafana не открывает лишнего наружу

Подпуть `/grafana/` открыт наружу целиком, поэтому Grafana стенда SHALL NOT отдавать свои метрики,
версию и коммит без входа, SHALL NOT открывать дашборды без входа (публичные дашборды и снимки выключены) и
SHALL NOT принимать basic auth: вход — только формой, на которую nginx интерфейса ставит
ограничение частоты.

#### Scenario: Метрики Grafana не отдаются наружу

- **WHEN** запрос без сессии приходит на `/grafana/metrics`
- **THEN** Grafana уводит на форму входа, `grafana_build_info` и счётчики пользователей не отдаются

#### Scenario: Basic auth не принимается

- **WHEN** запрос к API Grafana под `/grafana/` приходит с заголовком `Authorization: Basic`, даже с верным паролем
- **THEN** ответ — `401`, попытка не засчитывается в блокировку логина
- **AND** после таких запросов вход формой с верным паролем проходит

#### Scenario: Версия Grafana не отдаётся без входа

- **WHEN** запрос без сессии приходит на `/grafana/api/health` или на страницу `/grafana/login`
- **THEN** в ответе `/grafana/api/health` нет полей `version` и `commit`
- **AND** в `buildInfo` страницы входа нет версии и коммита

#### Scenario: Публичные дашборды выключены

- **WHEN** Admin или Editor пытается открыть дашборд наружу («Share externally», `POST /grafana/api/dashboards/uid/<uid>/public-dashboards`)
- **THEN** ответ — `404`, публичный дашборд не создаётся
- **AND** запрос без сессии к `/grafana/api/public/dashboards/<token>` получает `401`

#### Scenario: Снимки выключены

- **WHEN** Admin или Editor создаёт снимок дашборда (Share → Snapshot, `POST /grafana/api/snapshots`)
- **THEN** ответ — `403`, снимок не создаётся, ни локальный, ни на `snapshots.raintank.io`
- **AND** запрос без сессии к `/grafana/api/snapshots/<key>` получает `403` и данных дашборда не получает, в том числе для снимка, созданного до выключения
