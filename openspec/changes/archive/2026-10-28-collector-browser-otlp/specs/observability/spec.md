# Spec Delta

## ADDED Requirements

### Requirement: Трейсы браузера — через публичный вход

Коллектор SHALL принимать трейсы браузера только через nginx интерфейса: кабинет шлёт их на свой
origin (`/otlp/v1/traces`), nginx проксирует на OTLP/HTTP коллектора. Для этого коллектор и
интерфейс SHALL стоять в отдельной `internal`-сети `dmc268-otlp`. В сеть входа `dmc268` коллектор
SHALL NOT подключаться, порт OTLP на хосте SHALL NOT публиковаться. Сеть `dmc268-otlp` SHALL
создаваться и при выключенном стеке: стек интерфейса подключается к ней по имени.

#### Scenario: Стек включён

- **WHEN** стенд развёрнут с `observability_enabled = true`
- **THEN** `dmc268-otelcol` стоит в `dmc268-backend`, `dmc268-observability` и `dmc268-otlp`, а не в `dmc268`
- **AND** у `dmc268-otlp` нет шлюза: из неё не выйти в интернет

#### Scenario: Стек выключен

- **WHEN** стенд развёрнут с `observability_enabled = false`
- **THEN** сеть `dmc268-otlp` есть, коллектора нет, и стек интерфейса разворачивается без ошибок
