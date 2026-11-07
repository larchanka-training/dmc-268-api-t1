## Why

Подпуть `/grafana/` открыт наружу целиком (change `grafana-public-access`), и ревью PR #372
нашло ещё три места, где он отдаёт лишнее или портит панели:

- версию и коммит Grafana отдаёт без входа и помимо `/metrics`: `GET /grafana/api/health` и
  `buildInfo` в HTML `/grafana/login`;
- публичные дашборды в Grafana 13.x включены по умолчанию: Admin или Editor кнопкой
  «Share externally» открывает дашборд с логами Loki и трейсами без входа, мимо требования
  «Без учётки не пускает»;
- запросы самой Grafana идут через nginx интерфейса и попадают в access-лог `dmc268-ui`, а
  панели «Вход (nginx)» «Обзора» считают их трафиком входа. Открытый «Обзор» — около 0,6 запроса
  в секунду с каждой вкладки, и на стенде с малым трафиком панель показывает в основном Grafana.

## What Changes

- Grafana стенда получает `GF_AUTH_ANONYMOUS_HIDE_VERSION=true` (версия и коммит не отдаются
  без входа) и `GF_PUBLIC_DASHBOARDS_ENABLED=false` (публичные дашборды выключены).
- Выражения панелей «Вход (nginx)» дашборда «Обзор dmc-268» отбрасывают строки access-лога с
  запросами к `/grafana/` — так же, как уже отбрасывают пробы blackbox.

## Capabilities

### New Capabilities

Нет.

### Modified Capabilities

- `observability`: требования «Подпуть Grafana не открывает лишнего наружу» и «Метрики входа
  nginx в Prometheus и в «Обзоре»».

## Impact

- `infra/observability.tf`, `infra/tests/observability.tftest.hcl`,
  `infra/observability/grafana/provisioning/dashboards/dmc268-overview.json`,
  `tests/infrastructure/test_observability_stack.py`, `infra/README.md`.
- Деплой пересоздаёт только `dmc268-grafana` (новый env). Тома, база и брокер не затрагиваются.
- Публичных дашбордов на стенде ещё нет: Grafana наружу не выходила, выключение ничего не ломает.
