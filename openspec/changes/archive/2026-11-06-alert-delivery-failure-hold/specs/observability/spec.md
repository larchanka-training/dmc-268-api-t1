## MODIFIED Requirements

### Requirement: Сломанная доставка в Telegram видна мимо Telegram

`AlertDeliveryFailing` SHALL гореть, когда за 10 минут выросло число неудачных отправок в Telegram, и SHALL уходить не в Telegram, а на тот же check heartbeat с суффиксом `/fail`: сразу и раз в час, пока горит. Пока он горит, Alertmanager SHALL NOT отправлять пинг `Watchdog`: check остаётся down до починки доставки. Адрес `/fail` SHALL приходить файлом из секрета heartbeat. Страницу статуса алерт SHALL NOT менять.

#### Scenario: Токен бота отозван

- **WHEN** Alertmanager не может отправить уведомление в Telegram
- **THEN** в пределах 10 минут горит `AlertDeliveryFailing`, и внешний сервис получает запрос на адрес `/fail` своего check

#### Scenario: Check не возвращается в up, пока доставка сломана

- **WHEN** горит `AlertDeliveryFailing`
- **THEN** `Watchdog` в Alertmanager подавлен, пинг на адрес heartbeat не уходит, и check внешнего сервиса остаётся down

#### Scenario: Heartbeat без секрета

- **WHEN** секрета heartbeat нет, и неудачно уходят только пинги `Watchdog`
- **THEN** `AlertDeliveryFailing` не горит, а файл адреса `/fail` содержит заглушку

#### Scenario: Доставка починилась

- **WHEN** неудачных отправок в Telegram нет 10 минут
- **THEN** `AlertDeliveryFailing` гаснет, и следующий пинг `Watchdog` возвращает check в up
