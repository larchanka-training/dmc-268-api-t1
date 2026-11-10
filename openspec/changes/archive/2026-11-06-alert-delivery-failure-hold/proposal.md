## Why

Ревью #380 (замечание Grinv). После `/fail` внешний check красный около 5 минут из каждого часа:
следующий пинг `Watchdog` возвращает его в up, и пару «down/up» легко принять за сетевой сбой, а
сломанная доставка в Telegram — та самая, ради которой алерт заведён, — снова проходит
незамеченной.

## What Changes

- Правило подавления в `alertmanager.yml`: пока горит `AlertDeliveryFailing`, `Watchdog` заглушён и
  пинг не уходит. Check остаётся down до починки доставки.
- Повтор `/fail` раз в час остаётся: check, вручную возвращённый в up, снова уходит в down.
- Локально адрес `/fail` compose строит из того же `ALERT_HEARTBEAT_URL`, как деплой на стенде;
  отдельная переменная `ALERT_HEARTBEAT_FAIL_URL` не нужна.

## Capabilities

### New Capabilities

### Modified Capabilities

- `observability`: сломанная доставка в Telegram держит check heartbeat красным, пока не починена.

## Impact

- `infra/observability/alertmanager.yml`, `docker-compose.yml`, `infra/README.md`,
  `tests/infrastructure/test_alerting.py`.
- Секретов и переменных стенда не меняет; деплой пересоздаёт Alertmanager с новым конфигом.
