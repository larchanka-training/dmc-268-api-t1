## 1. Инфраструктура

- [x] 1.1 Grafana в `infra/observability.tf`: `GF_AUTH_ANONYMOUS_HIDE_VERSION=true` и `GF_PUBLIC_DASHBOARDS_ENABLED=false`
- [x] 1.2 Панели «Вход (nginx)» в `dmc268-overview.json`: фильтр строк с запросами к `/grafana/`
- [x] 1.3 Проверка образа Grafana 13.2.4 с этим env запуском: `/grafana/api/health` и `buildInfo` без версии и коммита, создание публичного дашборда — `404`, анонимный запрос к публичному дашборду — `401`

## 2. Тесты

- [x] 2.1 `infra/tests/observability.tftest.hcl`: оба env в `grafana_serves_public_entry_sub_path`
- [x] 2.2 `tests/infrastructure/test_observability_stack.py`: выражения по `dmc268-ui` отбрасывают пробы blackbox и запросы к `/grafana/`

## 3. Документация

- [x] 3.1 `infra/README.md`: оба env с причиной, фильтр панелей входа
