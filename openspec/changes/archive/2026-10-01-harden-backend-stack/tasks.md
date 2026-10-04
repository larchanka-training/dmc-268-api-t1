# Tasks

## 1. Образ

- [x] 1.1 Добавить в `Dockerfile` непривилегированного пользователя и `USER`; проверить, что образ собирается и `uvicorn` стартует

## 2. Конфигурация

- [x] 2.1 `api` и `migrate`: `read_only`, `tmpfs` для `/tmp`, `capabilities { drop = ["ALL"] }`, `no-new-privileges`, `init`
- [x] 2.2 `postgres` и `rabbitmq`: `no-new-privileges`
- [x] 2.3 Всем четырём контейнерам: `memory`, `memory_swap` и ротация логов
- [x] 2.4 `validation` для `bind_ip` и `internal_bind_ip`: только петля
- [x] 2.5 Провайдер до `~> 4.6`, `infra/.terraform.lock.hcl` на пять платформ; команду пересборки lock-файла записать в `infra/README.md`

## 3. Приёмка

- [ ] 3.1 Зелёный job `terraform` в CI (`fmt -check`, `init`, `validate`)
- [ ] 3.2 Деплой проходит, стенд отвечает по `/health`
- [ ] 3.3 Повторный деплой того же коммита даёт `No changes` — в том числе у `migrate` на провайдере v4
- [ ] 3.4 На стенде `docker inspect` показывает у `dmc268-api` непустого пользователя, `ReadonlyRootfs: true` и лимит памяти

## Примечания к выполнению

1.1 проверено локально (Docker 29.8.1, arm64) с флагами, как на стенде: `--read-only`, `--tmpfs /tmp`, `--cap-drop ALL`, `no-new-privileges`, `--init`, лимиты памяти. `migrate` от `uid=10001(app)` применил миграции до `0002 (head)` против `postgres:18-alpine` с `no-new-privileges`; API отдаёт `{"status":"ok"}`, команда healthcheck из `main.tf` проходит, запись в `/app` отклоняется (`Read-only file system`), `/tmp` доступен, `CapEff: 0`, `NoNewPrivs: 1`, ошибок в логах нет.

Пункты 3.2–3.4 выполнимы только после мёржа в `develop`: деплой катит только оттуда. Change заархивирован до приёмки на стенде; результат первого деплоя и повторного прогона того же коммита дописать сюда.
