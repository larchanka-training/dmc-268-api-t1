# Proposal

## Why

Сторож томов в `scripts/deploy_report.py` (#210) ищет уничтожение тома в JSON-плане OpenTofu только по адресу ресурса из `PROTECTED`. Обход: ресурс тома переименовали без блока `moved`, а `PROTECTED` поправили под новый адрес — в плане `delete` у старого адреса, которого в списке нет, и сторож молчит. `prevent_destroy` тоже не спасает: старый ресурс ушёл из конфигурации вместе со своим `lifecycle`. Закрывает #216.

## What Changes

- `PROTECTED` — адрес ресурса → имя тома в Docker: `dmc268-postgres-pgvector-data` и `dmc268-rabbitmq-v44-data` из `infra/*.tf`.
- Сторож отклоняет `delete` (и замену — `delete`, `create`) у любого `docker_volume`, у которого `change.before.name` — имя защищённого тома, по какому бы адресу он ни шёл. Проверка по адресу остаётся.
- Тест сверяет имена в `PROTECTED` с `name` ресурсов `docker_volume` в `infra/*.tf`.
- `infra/README.md`, «Тома данных деплой не уничтожает»: сторож узнаёт том и по имени.

Во фронте (`dmc-268-ui-t1`, `scripts/deploy-report.mjs`) то же для тома ACME уже сделано.

## Capabilities

### New Capabilities

Нет.

### Modified Capabilities

Нет.

`skip_specs: true`: требование `backend-delivery` «Развёртывание не уничтожает тома данных» не меняется, закрывается обход его проверки.

## Impact

- `scripts/deploy_report.py`, `tests/test_deploy_report.py`, `infra/README.md`.
