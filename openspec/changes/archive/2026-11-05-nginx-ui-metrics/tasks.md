## 1. Сбор и панели

- [x] 1.1 Receiver `nginx` в `otelcol.yaml` и в конвейере `metrics`; имена метрик сверены экспортом коллектора 0.158.0
- [x] 1.2 Панели «Вход (nginx)» в `dmc268-overview.json`; LogQL доли по классу кода проверен на Loki стенда
- [x] 1.3 Тесты дашборда: datasource Loki разрешён, сигналы nginx в выражениях
- [x] 1.4 `infra/README.md`: сеть `dmc268-otlp` и строка в таблице метрик

## 2. Завершение

- [x] 2.1 `otelcol validate`, `ruff`, `pytest`, `openspec validate --all --strict`
- [x] 2.2 Синхронизация спеки и `openspec archive nginx-ui-metrics`
