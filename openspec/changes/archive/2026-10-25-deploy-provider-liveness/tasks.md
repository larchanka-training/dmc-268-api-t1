## 1. Тесты (красные до правки)

- [x] 1.1 `infra/tests/main.tftest.hcl`: `wait`/`wait_timeout` у API, `start_interval = "2s"`, длительности healthcheck в формате Go, healthcheck и `terraform_data.api_healthcheck` из одного описания; мутации (`"2000ms"`, `"1m"`, `wait = false`, литерал в блоке) роняют тест

## 2. Конфигурация

- [x] 2.1 `infra/app.tf`: `wait = true`, `wait_timeout = 90`, `local.api_healthcheck` с `start_interval = "2s"`, `terraform_data.api_healthcheck` и `replace_triggered_by`

## 3. Деплой

- [x] 3.1 `deploy.yml`: шаг логов API при упавшем apply
- [x] 3.2 `check-api.sh`: 10 попыток раз в секунду вместо минуты

## 4. Документация и проверка

- [x] 4.1 `infra/README.md`: готовность API ждёт apply
- [x] 4.2 Локальный Docker: apply падает через 90 с на образе, который не становится healthy; повторный plan — `No changes`; правка healthcheck пересоздаёт API
- [x] 4.3 `tofu fmt`/`validate`/`test`, actionlint, zizmor, shellcheck, ruff, pytest, `openspec validate --all --strict`
