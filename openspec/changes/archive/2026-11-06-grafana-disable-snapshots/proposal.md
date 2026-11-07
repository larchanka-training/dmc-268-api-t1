## Why

Подпуть `/grafana/` открыт наружу целиком (change `grafana-public-access`), и ревью PR #372
нашло ещё одно место, где дашборды открываются без входа: снимки (snapshots). В Grafana они
включены по умолчанию, `GET /grafana/api/snapshots/<key>` и `/grafana/dashboard/snapshot/<key>`
отдаются без сессии, так что Admin или Editor через Share → Snapshot публикует панели с логами
Loki и трейсами для любого со ссылкой. Внешние снимки (`external_enabled`, по умолчанию `true`)
одной кнопкой уходят на `snapshots.raintank.io`. Это расходится с требованием «SHALL NOT
открывать дашборды без входа».

## What Changes

- Grafana стенда получает `GF_SNAPSHOTS_ENABLED=false` (API снимков отвечает `403`, в том числе
  на созданные раньше) и `GF_SNAPSHOTS_EXTERNAL_ENABLED=false` (это отдельная настройка, без неё
  интерфейс предлагает публикацию на raintank).

## Capabilities

### New Capabilities

Нет.

### Modified Capabilities

- `observability`: требование «Подпуть Grafana не открывает лишнего наружу».

## Impact

- `infra/observability.tf`, `infra/tests/observability.tftest.hcl`, `infra/README.md`.
- Деплой пересоздаёт только `dmc268-grafana` (новый env). Тома, база и брокер не затрагиваются.
- Снимков на стенде ещё нет: Grafana наружу не выходила, выключение ничего не ломает.
