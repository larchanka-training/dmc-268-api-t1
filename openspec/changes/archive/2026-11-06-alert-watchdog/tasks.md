# Tasks

## 1. Правило и маршрут

- [x] 1.1 `alerts.yml`: `Watchdog` (`vector(1)`, `severity: none`) горит всегда
- [x] 1.2 `alerts.test.yml`: `Watchdog` горит
- [x] 1.3 `alertmanager.yml`: первый маршрут `alertname="Watchdog"` на receiver `heartbeat` (webhook с `url_file`, повтор раз в 5 минут, без `send_resolved`)

## 2. Секрет и развёртывание

- [x] 2.1 Переменная `alert_heartbeat_url`: необязательная, только `https://`; `upload` в `/etc/alertmanager/secrets/heartbeat_url`, без секрета заглушка `unset`
- [x] 2.2 `deploy.yml`: `TF_VAR_alert_heartbeat_url` из секрета `ALERT_HEARTBEAT_URL_DMC268_T1`, предупреждение без него
- [x] 2.3 `docker-compose.yml`: тот же файл из `ALERT_HEARTBEAT_URL`
- [x] 2.4 Тесты: `test_alerting.py` (маршрут, пути секрета, `Watchdog` в README), `test_service_status.py` (страницу не трогает), `observability.tftest.hcl` (заглушка, битый URL, файл вместо env)
- [x] 2.5 `infra/README.md`: в разделе «Алерты» описан heartbeat, внешний сервис необязателен, есть его настройка и окно оповещения
