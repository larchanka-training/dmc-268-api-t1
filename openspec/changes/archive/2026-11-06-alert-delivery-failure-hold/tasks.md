## 1. Подавление и адрес

- [x] 1.1 Правило подавления `AlertDeliveryFailing` → `Watchdog` в `alertmanager.yml`; `amtool check-config`
- [x] 1.2 `docker-compose.yml`: адрес `/fail` из `ALERT_HEARTBEAT_URL`
- [x] 1.3 `tests/infrastructure/test_alerting.py`: правило подавления и адрес `/fail` в compose

## 2. Документы и завершение

- [x] 2.1 `infra/README.md`: check держится down, локальный адрес `/fail`, какие сервисы понимают `/fail`
- [x] 2.2 `make check`, `pytest`, синхронизация спеки и `openspec archive alert-delivery-failure-hold`
