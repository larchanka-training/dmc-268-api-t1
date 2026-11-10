## Why

Биллинг (#286, PR #396) смержен, но `BILLING_ENABLED` не проброшен в деплой: в `infra/` нет ни
переменной, ни строки в окружении воркера. На стенде флаг не включить без правки кода. Плательщики
всех подключённых репозиториев стенда назначены, стартовые балансы зачислены.

## What Changes

- Переменная OpenTofu `billing_enabled` (по умолчанию `false`) доходит до воркера как
  `BILLING_ENABLED`. API флаг не нужен: баланс сверяет только воркер.
- Деплой стенда передаёт `TF_VAR_billing_enabled: "true"`, как `TF_VAR_llm_backend`.
- `tofu test` проверяет, что без переменной воркер получает `BILLING_ENABLED=false`.

## Impact

- `infra/variables.tf`, `infra/app.tf`, `infra/tests/main.tftest.hcl`, `.github/workflows/deploy.yml`,
  `infra/README.md`.
- После деплоя прогон репозитория без плательщика или без денег получает `failure`
  «Недостаточно средств». Откат: `TF_VAR_billing_enabled` убрать и передеплоить.
