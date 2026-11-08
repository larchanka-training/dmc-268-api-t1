## 1. Правки

- [x] 1.1 `billing_enabled` в `infra/variables.tf`, `BILLING_ENABLED` в окружении `docker_container.worker`
- [x] 1.2 `TF_VAR_billing_enabled: "true"` в `.github/workflows/deploy.yml`, строка в таблице `infra/README.md`
- [x] 1.3 `tofu test`: без переменной воркер получает `BILLING_ENABLED=false`
