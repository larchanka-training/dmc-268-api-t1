## Why

Если не работают сами алерты, в Telegram ничего не приходит, и это не отличить от спокойного дня (#334, продолжение #100). Так бывает, когда упал `dmc268-prometheus` или `dmc268-alertmanager` или у них пропал выход наружу. `ContainerDown` за Prometheus и Alertmanager не следит, а `ScrapeTargetDown` по Alertmanager пришлось бы отправлять самому упавшему Alertmanager. Сейчас это ограничение только описано в `infra/README.md` вместе с ручной проверкой.

## What Changes

- Правило `Watchdog` в `infra/observability/alerts.yml`: `expr: vector(1)`, горит всегда (`severity: none`).
- В `alertmanager.yml` первым стоит маршрут `alertname="Watchdog"` на receiver `heartbeat`. Это webhook на внешний heartbeat-сервис (healthchecks.io или аналог), он шлёт раз в 5 минут. В Telegram Watchdog не попадает.
- Внешний сервис шумит в канал команды, когда пинги перестают приходить.
- URL heartbeat — необязательный секрет `ALERT_HEARTBEAT_URL_DMC268_T1`, по образцу `TELEGRAM_*`:
  - Alertmanager получает его файлом (`url_file`);
  - без секрета деплой зелёный, получатель заглушка `unset`, пинг падает с ошибкой в логе, а прогон предупреждает аннотацией;
  - не-`https://` значение роняет `plan`.
- Страница статуса Watchdog не учитывает: он не входит в `ALERT_RULES`.
- **Не входит:** сломанная доставка в Telegram (отозванный токен) — пинг идёт мимо Telegram. Для неё остаётся ручная проверка тестовым алертом из `infra/README.md`.

## Capabilities

### New Capabilities

### Modified Capabilities

- `observability`: появляется dead man's switch для алертов. Это всегда горящий `Watchdog`, его маршрут на внешний heartbeat и необязательный секрет его адреса.

## Impact

- Изменённые файлы:
  - `infra/observability/alerts.yml`, `alerts.test.yml`, `alertmanager.yml`;
  - `infra/observability.tf`, `infra/variables.tf`, `infra/terraform.tfvars.example`, `infra/tests/observability.tftest.hcl`;
  - `docker-compose.yml`;
  - `.github/workflows/deploy.yml`;
  - `infra/README.md`;
  - `tests/infrastructure/test_alerting.py`, `tests/domain/test_service_status.py`.
- Настройки репозитория: новый необязательный секрет `ALERT_HEARTBEAT_URL_DMC268_T1`. Учётка во внешнем сервисе необязательна: без неё деплой проходит, не будет только оповещения о падении самого стека алертов. Её настройка и проверка на стенде (остановить Alertmanager и дождаться сообщения) — вне этого change, по `infra/README.md`.
- Нагрузка: один POST наружу раз в 5 минут.
