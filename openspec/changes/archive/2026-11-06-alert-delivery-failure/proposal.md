## Why

Ревью #376 (dead man's switch, #334), два замечания не блокером. Сломанная доставка в Telegram —
отозванный после ротации токен бота, бот, удалённый из канала, — остаётся тупиком: пинг `Watchdog`
идёт мимо Telegram, и все алерты пропадают молча, пока кто-то не вспомнит про тестовый алерт.
Второе: спека обещает пинг «не реже раза в 5 минут», а при `group_interval: 1m` и
`repeat_interval: 5m` маршрута `Watchdog` он уходит раз в 5–6 минут; README и комментарий в
`alertmanager.yml` это уже пишут.

## What Changes

- Алерт `AlertDeliveryFailing` (critical, `service: alertmanager`): за 10 минут выросло
  `alertmanager_notifications_failed_total{integration="telegram"}`. Неудачи других интеграций
  (heartbeat без секрета) его не зажигают.
- Второй маршрут Alertmanager, без `continue`: `AlertDeliveryFailing` уходит не в Telegram, а на
  receiver `heartbeat-fail` — тот же check внешнего сервиса с суффиксом `/fail`. Сразу
  (`group_wait: 0s`), повтор раз в час.
- Адрес `/fail` — файл `/etc/alertmanager/secrets/heartbeat_fail_url`: деплой строит его из
  секрета `ALERT_HEARTBEAT_URL_DMC268_T1` (`trimsuffix(…, "/")` + `/fail`), без секрета — заглушка
  `unset`. Локально — `ALERT_HEARTBEAT_FAIL_URL` в compose.
- Спека `Dead man's switch`: «раз в 5–6 минут» вместо «не реже раза в 5 минут».

## Capabilities

### New Capabilities

### Modified Capabilities

- `observability`: алерт сломанной доставки в Telegram мимо Telegram; честный интервал пинга.

## Impact

- `infra/observability/alerts.yml`, `alerts.test.yml`, `alertmanager.yml`, `infra/observability.tf`,
  `infra/tests/observability.tftest.hcl`, `docker-compose.yml`, `infra/README.md`.
- `tests/infrastructure/test_alerting.py`, `tests/domain/test_service_status.py`: страницу статуса
  новый алерт не трогает (его нет в `ALERT_RULES`).
- Новых секретов нет. Деплой пересоздаёт Alertmanager: у него новый файл.
