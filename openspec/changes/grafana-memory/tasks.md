# Tasks

## 1. Потолок

- [x] 1.1 `observability_memory_mb.grafana` = 1024 в `infra/variables.tf`; таблица, итоги и запас в `infra/README.md`

## 2. Стенд

- [ ] 2.1 План деплоя заменяет только `dmc268-grafana` (и `api`, `worker` по новому образу)
- [ ] 2.2 В Explore открывается трейс API, «Related logs» показывает его строку в Loki; `RestartCount` Grafana не растёт, в `dmesg` нет новых OOM
