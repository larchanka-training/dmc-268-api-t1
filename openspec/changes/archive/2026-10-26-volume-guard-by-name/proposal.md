# Proposal

## Why

Сторож томов в `scripts/deploy_report.py` (#210) ищет уничтожение тома в JSON-плане OpenTofu только по адресу ресурса из `PROTECTED`. Обход: ресурс тома переименовали без блока `moved`, а `PROTECTED` поправили под новый адрес — в плане `delete` у старого адреса, которого в списке нет, и сторож молчит. `prevent_destroy` тоже не спасает: старый ресурс ушёл из конфигурации вместе со своим `lifecycle`. Закрывает #216.

## What Changes

- Сторож отклоняет `delete` (и замену — `delete`, `create`) у любого `docker_volume`, по какому бы адресу он ни шёл. Список `PROTECTED` удалён: проверка по имени тома (как во фронте, `scripts/deploy-report.mjs`) тоже обходится — переименовать ресурс и дать тому новое имя, поправив список. Все тома стека — с данными; незащищённых томов нет.
- Смена тома — `removed { lifecycle { destroy = false } }`: план выходит `forget`, сторож его пропускает.
- Тест проверяет `prevent_destroy` у каждого `docker_volume` в `infra/*.tf`, а не по списку.
- `infra/README.md`, «Тома данных деплой не уничтожает».

## Capabilities

### New Capabilities

Нет.

### Modified Capabilities

Нет.

`skip_specs: true`: требование `backend-delivery` «Развёртывание не уничтожает тома данных» не меняется, закрывается обход его проверки.

## Impact

- `scripts/deploy_report.py`, `tests/test_deploy_report.py`, `infra/README.md`.
