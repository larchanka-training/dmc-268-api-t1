## 1. Тесты конфигурации

- [x] 1.1 `infra/tests/main.tftest.hcl` с `mock_provider "docker"`: адреса базы и брокера в окружении `migrate`, `api`, `worker` (urlencode, имена контейнеров, vhost `%2F`); секреты API не попадают в `migrate` и `worker`; сеть, порты, healthcheck; грейс-период воркера с фоллбэком и без; один образ API у трёх контейнеров (`apply`); упавшая миграция (`override_resource`); адреса публикации вне петли
- [x] 1.2 Красные прогоны до проверок переменных: пустые секреты, `ollama` без модели, фоллбэк без Eurorouter, `api_image` без digest — `Missing expected failure`
- [x] 1.3 Мутации `main.tf` (без urlencode, без `OLLAMA_MODEL`, постусловие без проверки кода) роняют тесты

## 2. Проверки переменных

- [x] 2.1 `infra/variables.tf`: непустые `github_webhook_secret`, `postgres_password`, `rabbitmq_password`; `ollama_model` при `ollama`; `llm_hosted_fallback` с `ollama` и всеми `eurorouter_*`; digest в `api_image`; `tofu test` зелёный на 1.13.2

## 3. Образ API по digest

- [x] 3.1 Провайдер 4.6.0 забирает `repo:tag@sha256:…` и `repo@sha256:…`, повторный `plan` — `No changes` (локальный Docker)
- [x] 3.2 `deploy.yml`: `id: build` у `docker/build-push-action`, выход job'а `image` — `<образ>:<sha>@<digest>`, проверка доступности по digest и отказ на пустом digest
- [x] 3.3 Комментарии со ссылкой на #150 у образов PostgreSQL и RabbitMQ

## 4. CI и документация

- [x] 4.1 Job `terraform` → `opentofu` в `ci.yml`, шаг `tofu test`; упоминаний `terraform` как job не осталось
- [x] 4.2 `infra/README.md`: почему OpenTofu (факты — opentofu.org), `tofu test`, проверки переменных, digest
- [x] 4.3 Сверка с кодом: комментарий pgvector в `main.tf`, `terraform.tfvars.example`, вызывающие в `.github/actions/s3-backend/action.yml`, «Terraform» в комментариях
- [x] 4.4 actionlint, zizmor, `tofu fmt -check -recursive`, `tofu validate`, `tofu test`
