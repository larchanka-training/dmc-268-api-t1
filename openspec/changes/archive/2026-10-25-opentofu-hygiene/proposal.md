# Proposal

## Why

Job `terraform` в CI проверяет только формат и синтаксис `infra/`: логику `main.tf` — адреса базы и брокера в окружении, кому какие секреты, сеть, порты, грейс-период воркера — не проверяет ничто. Пустой секрет вебхука (незаданный секрет CI приходит в `TF_VAR_*` пустой строкой) и `llm_backend = "ollama"` без модели доходят до `apply`: стенд пересоздаётся и падает уже после. Образ API забирается по тегу, а тег перезаписывается. Job называется `terraform`, хотя стек — OpenTofu (#162).

## What Changes

- `infra/tests/main.tftest.hcl`: `tofu test` с `mock_provider "docker"` — 12 прогонов без Docker и сервера; шаг `tofu test` в job CI.
- Проверки переменных: непустые `github_webhook_secret`, `postgres_password`, `rabbitmq_password`; `ollama_model` при `llm_backend = "ollama"`; `llm_hosted_fallback` только с `ollama` и всеми `eurorouter_*`; `api_image` только с digest.
- `deploy.yml`: образ API передаётся в `tofu` как `<репозиторий>:<sha>@<digest>` из `outputs.digest` у `docker/build-push-action`.
- Job `terraform` переименован в `opentofu`; в `infra/README.md` — почему OpenTofu.
- Устаревшие комментарии: pgvector в `main.tf`, `terraform.tfvars.example` (нет секрета вебхука и `llm_backend` среди обязательных), вызывающие composite action `s3-backend`.

**Не входит:** digest образов PostgreSQL и RabbitMQ — пересоздание без `destroy_grace_seconds` убивает их SIGKILL (#150, #102). Правка rulesets `develop` и `main` (обязательная проверка `terraform` → `opentofu`) — админ, в момент мёржа.

## Capabilities

### Modified Capabilities

- `backend-delivery`: проверка конфигурации развёртывания в CI, отказ на этапе планирования при пустых секретах и неполном выборе модели, образ API по digest.

## Impact

- `infra/variables.tf`, `infra/main.tf` (комментарии), `infra/tests/`, `infra/terraform.tfvars.example`, `infra/README.md`.
- `.github/workflows/ci.yml` (job `opentofu`), `.github/workflows/deploy.yml`, `.github/actions/s3-backend/action.yml`, `.github/scripts/render-s3-script.sh` (комментарии).
- Обязательная проверка `terraform` в rulesets пропадает из прогонов: без правки ruleset мёрж блокируется.
- Сборка с provenance-аттестацией даёт новый digest и на повторной сборке того же коммита: перезапуск деплоя пересоздаёт `migrate`, `api` и `worker`.
