## ADDED Requirements

### Requirement: Dead man's switch алертов

Prometheus SHALL держать всегда горящий алерт `Watchdog`, а Alertmanager SHALL отправлять его не в Telegram, а на внешний heartbeat-сервис, повторяя не реже раза в 5 минут. Внешний сервис SHALL оповещать канал команды, когда пинги перестают приходить. `Watchdog` SHALL гаснуть, если за последние 15 минут Alertmanager не смог доставить уведомление в Telegram. `Watchdog` SHALL NOT менять публичный статус сервиса.

#### Scenario: Стек алертов работает

- **WHEN** Prometheus и Alertmanager работают, а доставка в Telegram не падала
- **THEN** внешний сервис получает пинг не реже раза в 5 минут, а в Telegram `Watchdog` не приходит

#### Scenario: Упал Prometheus или Alertmanager

- **WHEN** на стенде остановлен `dmc268-prometheus` или `dmc268-alertmanager`
- **THEN** пинги прекращаются, и внешний сервис оповещает канал команды в пределах 20 минут

#### Scenario: Сломалась доставка в Telegram

- **WHEN** Alertmanager не смог доставить алерт в Telegram, например токен бота отозван
- **THEN** `Watchdog` гаснет на 15 минут, пинги прекращаются, и внешний сервис оповещает канал команды

#### Scenario: Watchdog и страница статуса

- **WHEN** горит только `Watchdog`
- **THEN** `GET /api/v1/status` отвечает `operational` по всем компонентам

### Requirement: Секрет heartbeat алертов

Адрес heartbeat SHALL приходить Alertmanager файлом, а не конфигом и не переменной окружения контейнера. Без него стек SHALL подниматься, а развёртывание SHALL проходить с предупреждением. Адрес не вида `https://…` SHALL останавливать развёртывание на планировании.

#### Scenario: Секрета heartbeat нет

- **WHEN** стенд разворачивается с `observability_enabled = true` без адреса heartbeat
- **THEN** стек поднимается, `Watchdog` горит в Alertmanager, пинг падает с ошибкой в его логе, а прогон деплоя показывает предупреждение

#### Scenario: Секрет heartbeat битый

- **WHEN** адрес heartbeat не начинается с `https://` или содержит пробел
- **THEN** развёртывание останавливается на этапе планирования, и на сервере ничего не меняется
