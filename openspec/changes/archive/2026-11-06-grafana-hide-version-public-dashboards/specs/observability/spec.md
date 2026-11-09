## MODIFIED Requirements

### Requirement: Метрики входа nginx в Prometheus и в «Обзоре»

Основной коллектор SHALL снимать `stub_status` nginx интерфейса receiver-ом `nginx` по сети `dmc268-otlp`, без публикации порта на хост, и отдавать метрики в Prometheus через свой exporter-порт. Дашборд «Обзор dmc-268» SHALL показывать вход: соединения по состоянию из Prometheus, запросы в секунду и долю ответов по классу кода из access-лога `dmc268-ui` в Loki, без проб blackbox и запросов к `/grafana/`. Недоступный `stub_status` SHALL NOT мешать остальным метрикам коллектора.

#### Scenario: Метрики nginx в Prometheus

- **WHEN** интерфейс отдаёт `stub_status`, и Prometheus скрейпит коллектор
- **THEN** есть `nginx_requests_total` и `nginx_connections_current` с лейблом `state`

#### Scenario: Интерфейса нет

- **WHEN** коллектор запущен в локальном compose, где интерфейса нет
- **THEN** скрейп `stub_status` пишет ошибку в лог коллектора, а метрики сервисов и базы идут как прежде

#### Scenario: Панели входа

- **WHEN** дашборд «Обзор dmc-268» открыт в Grafana
- **THEN** панели «Вход (nginx)» показывают запросы в секунду, соединения по состоянию и долю ответов 2xx, 3xx, 4xx и 5xx

#### Scenario: Запросы Grafana не считаются входом

- **WHEN** в access-логе `dmc268-ui` есть строки с запросами к `/grafana/`: Grafana ходит через тот же nginx, в том числе сам открытый «Обзор»
- **THEN** панели «Вход (nginx)» из Loki их не считают, как и пробы blackbox

### Requirement: Подпуть Grafana не открывает лишнего наружу

Подпуть `/grafana/` открыт наружу целиком, поэтому Grafana стенда SHALL NOT отдавать свои метрики,
версию и коммит без входа, SHALL NOT открывать дашборды без входа (публичные дашборды выключены) и
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
