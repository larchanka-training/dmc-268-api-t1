## 1. Правила

- [x] 1.1 `WorkerStalled`, `ReviewJobStuck`, `ContainerMemoryNearLimit` в `infra/observability/alerts.yml`
- [x] 1.2 `alerts.test.yml`: горит и молчит на каждый алерт, рестарт брокера, поток прогонов подряд, нет потребителя, лимит 0
- [x] 1.3 Строки таблицы алертов в `infra/README.md`
- [x] 1.4 `ReviewDlqNotEmpty` не глушит предупреждения (`alertmanager.yml`, `inhibit_rules`): он горит до ручного разбора DLQ
- [x] 1.5 Тест promtool: одна зависшая задача при пустой очереди: горит только `ReviewJobStuck`

## 2. Страница статуса

- [x] 2.1 Строки `ALERT_RULES` (`app/domain/service_status.py`) для `WorkerStalled` и `ReviewJobStuck` и тесты `tests/domain/test_service_status.py`; `ContainerMemoryNearLimit` (warning) страницу не трогает
- [x] 2.2 Перечень статусов в delta `service-status` открытого change `add-status-page`
- [x] 2.3 Лимит памяти `dmc268-blackbox` и `dmc268-blackbox-public` 32 → 48 МиБ (`infra/variables.tf`, таблица `infra/README.md`): по истории стенда за 7 дней `ContainerMemoryNearLimit` горел бы у них без OOM
- [x] 2.4 `WorkerStalled` на странице статуса даёт `degraded`, а не `outage`: медленную модель с фоллбэком от зависания по метрикам не отличить
- [x] 2.5 Абзац о памяти в `infra/README.md`: в сумму потолков входят `dmc268-s3` (512) и `dmc268-ui` (128), потолки превышают память VPS примерно на 210 МБ

## 3. Завершение

- [x] 3.1 `promtool check config`, `promtool test rules`, `ruff`, `lint-imports`, `mypy`, `pytest`, `openspec validate --all --strict`
- [x] 3.2 Синхронизация спек и `openspec archive worker-monitoring-alerts`
