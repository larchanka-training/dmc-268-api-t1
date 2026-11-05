## ADDED Requirements

### Requirement: Метрики входа nginx в Prometheus и в «Обзоре»

Основной коллектор SHALL снимать `stub_status` nginx интерфейса receiver-ом `nginx` по сети `dmc268-otlp`, без публикации порта на хост, и отдавать метрики в Prometheus через свой exporter-порт. Дашборд «Обзор dmc-268» SHALL показывать вход: соединения по состоянию из Prometheus, запросы в секунду и долю ответов по классу кода из access-лога `dmc268-ui` в Loki, без проб blackbox. Недоступный `stub_status` SHALL NOT мешать остальным метрикам коллектора.

#### Scenario: Метрики nginx в Prometheus

- **WHEN** интерфейс отдаёт `stub_status`, и Prometheus скрейпит коллектор
- **THEN** есть `nginx_requests_total` и `nginx_connections_current` с лейблом `state`

#### Scenario: Интерфейса нет

- **WHEN** коллектор запущен в локальном compose, где интерфейса нет
- **THEN** скрейп `stub_status` пишет ошибку в лог коллектора, а метрики сервисов и базы идут как прежде

#### Scenario: Панели входа

- **WHEN** дашборд «Обзор dmc-268» открыт в Grafana
- **THEN** панели «Вход (nginx)» показывают запросы в секунду, соединения по состоянию и долю ответов 2xx, 3xx, 4xx и 5xx
