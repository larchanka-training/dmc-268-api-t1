## MODIFIED Requirements

### Requirement: Dead man's switch алертов

Prometheus SHALL держать всегда горящий алерт `Watchdog`, а Alertmanager SHALL отправлять его не в Telegram, а на внешний heartbeat-сервис, повторяя раз в 5–6 минут. Внешний сервис SHALL оповещать канал команды, когда пинги перестают приходить. `Watchdog` SHALL NOT менять публичный статус сервиса.

#### Scenario: Стек алертов работает

- **WHEN** Prometheus и Alertmanager работают
- **THEN** внешний сервис получает пинг раз в 5–6 минут, а в Telegram `Watchdog` не приходит

#### Scenario: Упал Prometheus или Alertmanager

- **WHEN** на стенде остановлен `dmc268-prometheus` или `dmc268-alertmanager`
- **THEN** пинги прекращаются, и внешний сервис оповещает канал команды в пределах 20 минут

#### Scenario: Watchdog и страница статуса

- **WHEN** горит только `Watchdog`
- **THEN** `GET /api/v1/status` отвечает `operational` по всем компонентам

## ADDED Requirements

### Requirement: Сломанная доставка в Telegram видна мимо Telegram

`AlertDeliveryFailing` SHALL гореть, когда за 10 минут выросло число неудачных отправок в Telegram, и SHALL уходить не в Telegram, а на тот же check heartbeat с суффиксом `/fail`: сразу и раз в час, пока горит. Адрес `/fail` SHALL приходить файлом из секрета heartbeat. Страницу статуса алерт SHALL NOT менять.

#### Scenario: Токен бота отозван

- **WHEN** Alertmanager не может отправить уведомление в Telegram
- **THEN** в пределах 10 минут горит `AlertDeliveryFailing`, и внешний сервис получает запрос на адрес `/fail` своего check

#### Scenario: Heartbeat без секрета

- **WHEN** секрета heartbeat нет, и неудачно уходят только пинги `Watchdog`
- **THEN** `AlertDeliveryFailing` не горит, а файл адреса `/fail` содержит заглушку

#### Scenario: Доставка починилась

- **WHEN** неудачных отправок в Telegram нет 10 минут
- **THEN** `AlertDeliveryFailing` гаснет
