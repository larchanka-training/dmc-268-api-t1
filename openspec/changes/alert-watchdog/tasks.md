# Tasks

## 1. Правило и маршрут

- [x] 1.1 `alerts.yml`: `Watchdog` (`severity: none`) горит всегда и гаснет при сбое доставки в Telegram за 15 минут
- [x] 1.2 `alerts.test.yml`: `Watchdog` горит, гаснет при сбое Telegram и не гаснет при сбое самого heartbeat
- [x] 1.3 `alertmanager.yml`: первый маршрут `alertname="Watchdog"` на receiver `heartbeat` (webhook с `url_file`, повтор раз в 5 минут, без `send_resolved`)

## 2. Секрет и развёртывание

- [x] 2.1 Переменная `alert_heartbeat_url`: необязательная, только `https://`; `upload` в `/etc/alertmanager/secrets/heartbeat_url`, без секрета заглушка `unset`
- [x] 2.2 `deploy.yml`: `TF_VAR_alert_heartbeat_url` из секрета `ALERT_HEARTBEAT_URL_DMC268_T1`, предупреждение без него
- [x] 2.3 `docker-compose.yml`: тот же файл из `ALERT_HEARTBEAT_URL`
- [x] 2.4 Тесты: `test_alerting.py` (маршрут, пути секрета, `Watchdog` в README), `test_service_status.py` (страницу не трогает), `observability.tftest.hcl` (заглушка, битый URL, файл вместо env)
- [x] 2.5 `infra/README.md`: в разделе «Алерты» heartbeat описан вместо ограничения, есть настройка внешнего сервиса и окно оповещения

## 3. Проверка на стенде

- [ ] 3.1 Заведён check во внешнем сервисе (период 5 минут, grace 10 минут) с оповещением в канал команды, секрет `ALERT_HEARTBEAT_URL_DMC268_T1` задан, деплой пересоздал Alertmanager
- [ ] 3.2 Пинги приходят во внешний сервис раз в 5 минут
- [ ] 3.3 Остановленный `dmc268-alertmanager`, а затем `dmc268-prometheus`, дают сообщение в канале в пределах 20 минут; после старта check снова зелёный
