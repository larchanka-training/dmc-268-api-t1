## 1. Инфраструктура

- [x] 1.1 Grafana в `infra/observability.tf`: `GF_SNAPSHOTS_ENABLED=false` и `GF_SNAPSHOTS_EXTERNAL_ENABLED=false`
- [x] 1.2 Проверка образа Grafana 13.2.4 запуском: без env снимок создаётся и отдаётся без входа, с env создание отклоняется и без входа снимок не отдаётся

## 2. Тесты

- [x] 2.1 `infra/tests/observability.tftest.hcl`: оба env в `grafana_serves_public_entry_sub_path`

## 3. Документация

- [x] 3.1 `infra/README.md`: оба env с причиной
