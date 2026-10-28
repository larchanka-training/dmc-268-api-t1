# Tasks

## 1. Сеть приёма трейсов браузера

- [x] 1.1 Сеть `dmc268-otlp` (`internal`, без флага стека) в `infra/network.tf`
- [x] 1.2 `dmc268-otelcol` в `dmc268-otlp` (`infra/observability.tf`)
- [x] 1.3 Тесты: сети коллектора и `internal` у `dmc268-otlp` — `tests/test_infra_isolation.py`, `infra/tests/observability.tftest.hcl`

## 2. Документы и приёмка

- [x] 2.1 `infra/README.md`: строка `dmc268-otlp` в таблице сетей
- [x] 2.2 ruff, lint-imports, mypy, pytest с `PYTEST_FAIL_ON_SKIP=1`, `tofu test`, openspec validate
