# Tasks

## 1. Блокировка состояния

- [x] 1.1 Проверить условную запись и `use_lockfile` на `rustfs/rustfs:1.0.0` локально (результаты в `design.md`)
- [x] 1.2 Включить `use_lockfile = true` в `infra/backend.tf`; описать в `infra/README.md` блокировку и `tofu force-unlock`

## 2. RabbitMQ 4.3

- [x] 2.1 Проверить обновление 3.13 → 4.3 на месте и по шагам локально (результаты в `design.md`)
- [x] 2.2 Образ `rabbitmq:4.3-alpine`, новый том `dmc268-rabbitmq-v4-data`
- [x] 2.3 Сообщить в PR #36, что `docker-compose.yml` и `ci.yml` должны перейти на `4.3-alpine`

## 3. Приёмка

- [x] 3.1 Зелёный job `terraform` в CI
- [ ] 3.2 Деплой проходит; на стенде `rabbitmqctl version` показывает 4.3, lock-объекта в бакете после деплоя нет
- [ ] 3.3 Повторный деплой того же коммита даёт `No changes`

## Примечания к выполнению

2.3: замечания оставлены в PR #36 у строк `docker-compose.yml:37` и `.github/workflows/ci.yml:68` ([ревью](https://github.com/larchanka-training/dmc-268-api-t1/pull/36#pullrequestreview-5372640870)).

3.1: [CI 36784604449](https://github.com/larchanka-training/dmc-268-api-t1/actions/runs/36784604449) зелёный. Пункты 3.2–3.3 выполнимы только после мёржа в `develop`; change заархивирован до приёмки на стенде, результат деплоя дописать сюда.
