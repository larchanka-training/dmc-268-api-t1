## ADDED Requirements

### Requirement: Grafana стенда — через публичный вход по учётке

Grafana стенда SHALL открываться через публичный вход по подпути `/grafana/` и SHALL знать свой
публичный адрес (`root_url` — публичный origin и `/grafana/`). Вход SHALL быть только по учётке:
анонимный вход и регистрация SHALL быть выключены. Cookie сессии SHALL выдаваться с атрибутом
`Secure`. Туннель на петле (`grafana_port` на `bind_ip`) SHALL продолжать работать по тому же
подпути.

#### Scenario: Вход через публичный адрес

- **WHEN** участник команды открывает `https://<публичный адрес>/grafana/`
- **THEN** Grafana показывает форму входа, и после входа личной учёткой открывается дашборд «Обзор dmc-268»
- **AND** cookie сессии `grafana_session` выдана с `Secure` и `HttpOnly` и путём `/grafana`

#### Scenario: Без учётки не пускает

- **WHEN** запрос к API Grafana под `/grafana/` приходит без сессии
- **THEN** ответ — `401`, данные дашбордов и datasource не отдаются
- **AND** зарегистрировать нового пользователя нельзя

#### Scenario: Туннель на петле

- **WHEN** участник с SSH-доступом открывает туннель на `grafana_port` и заходит на `http://localhost:3000/grafana/`
- **THEN** Grafana отвечает формой входа так же, как через публичный вход

### Requirement: Подпуть Grafana не открывает лишнего наружу

Подпуть `/grafana/` открыт наружу целиком, поэтому Grafana стенда SHALL NOT отдавать свои метрики
без входа и SHALL NOT принимать basic auth: вход — только формой, на которую nginx интерфейса
ставит ограничение частоты.

#### Scenario: Метрики Grafana не отдаются наружу

- **WHEN** запрос без сессии приходит на `/grafana/metrics`
- **THEN** Grafana уводит на форму входа, `grafana_build_info` и счётчики пользователей не отдаются

#### Scenario: Basic auth не принимается

- **WHEN** запрос к API Grafana под `/grafana/` приходит с заголовком `Authorization: Basic`, даже с верным паролем
- **THEN** ответ — `401`, попытка не засчитывается в блокировку логина
- **AND** после таких запросов вход формой с верным паролем проходит

### Requirement: Сеть прокси Grafana

nginx интерфейса SHALL ходить в Grafana по отдельной `internal`-сети `dmc268-grafana-proxy`
(`dmc268-grafana:3000`), где SHALL стоять только Grafana и интерфейс. Сеть SHALL создаваться и при
выключенном стеке: стек интерфейса подключается к ней по имени.

#### Scenario: Стек включён

- **WHEN** стенд развёрнут с `observability_enabled = true`
- **THEN** в `dmc268-grafana-proxy` из стека бэкенда стоит только `dmc268-grafana`
- **AND** у `dmc268-grafana-proxy` нет шлюза: из неё не выйти в интернет

#### Scenario: Стек выключен

- **WHEN** стенд развёрнут с `observability_enabled = false`
- **THEN** сеть `dmc268-grafana-proxy` есть, Grafana нет, и стек интерфейса разворачивается без ошибок
