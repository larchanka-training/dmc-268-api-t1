## 1. Алерт и маршрут

- [x] 1.1 `AlertDeliveryFailing` в `infra/observability/alerts.yml` и тесты promtool: горит на неудачах Telegram, молчит на неудачах webhook, гаснет после починки
- [x] 1.2 Второй маршрут и receiver `heartbeat-fail` в `alertmanager.yml`; `amtool check-config`
- [x] 1.3 Файл `heartbeat_fail_url` в `infra/observability.tf` и `docker-compose.yml`; `tofu test`: заглушка без секрета, `/fail` без двойного `/`
- [x] 1.4 `tests/infrastructure/test_alerting.py`: маршрут, receiver, пути секретов; `tests/domain/test_service_status.py`: страница статуса не меняется

## 2. Документы

- [x] 2.1 `infra/README.md`: строка таблицы алертов, «Чего алерты не видят», раздел «Dead man's switch», переменная `alert_heartbeat_url`
- [x] 2.2 Интервал пинга `Watchdog` в спеке — раз в 5–6 минут

## 3. Завершение

- [x] 3.1 `promtool`, `amtool`, `tofu test`, `make check`, `pytest`
- [x] 3.2 Синхронизация спек и `openspec archive alert-delivery-failure`
