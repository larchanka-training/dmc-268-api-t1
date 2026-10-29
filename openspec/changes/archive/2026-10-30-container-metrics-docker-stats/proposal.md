# Proposal

## Why

На стенде панели «CPU контейнеров» и «Память контейнеров» пустые (#249): cAdvisor v0.52.1 не видит
ни одного контейнера. Docker 29 на стенде хранит образы в containerd snapshotter, каталога
`/var/lib/docker/image/overlayfs/layerdb` нет, и cAdvisor не находит слой контейнера. Новые версии
(v0.54+, вплоть до v0.60.6) без сокета containerd фабрику docker не регистрируют — исправление
google/cadvisor#3910 не смёржено, а сокет containerd — полный доступ к хосту, от которого cAdvisor
увели на ревью PR #130. Часть #108 (стек наблюдаемости на проде).

## What Changes

- cAdvisor убран из `docker-compose.yml` и `infra/` (контейнер, образ, потолок памяти, job
  Prometheus).
- Метрики контейнеров снимает receiver `docker_stats` коллектора через тот же socket-proxy:
  коллектор подключён к сети proxy, в метриках — общий конвейер. id, hostname и образ контейнера
  удаляются из ресурса: меняются каждым деплоем.
- Белый список socket-proxy: плюс `/containers/{id}/stats` и `/events` (без событий новые
  контейнеры не находятся — проверено запуском через шаблон).
- Панели дашборда — на `container_cpu_usage_nanoseconds_total` и
  `container_memory_usage_total_bytes` по `container_name`.
- `infra/README.md`: раздел «Метрики контейнеров» — почему не cAdvisor; таблица памяти без
  cAdvisor (стек 3328 МБ, стенд 6656 МБ).

## Capabilities

### New Capabilities

Нет.

### Modified Capabilities

- `observability`: стек из семи контейнеров; метрики контейнеров — receiver `docker_stats`.

## Impact

- `infra/observability.tf`, `infra/locals.tf`, `infra/variables.tf`, `infra/observability/*`,
  `docker-compose.yml`, тесты стека и изоляции сетей, `infra/tests/observability.tftest.hcl`,
  `infra/README.md`, `docs/BACKEND_ARCHITECTURE.md`, `docs/testing/TEST_PLAN.md`.
- Деплой удаляет `dmc268-cadvisor` и пересоздаёт коллектор и socket-proxy; база и брокер не
  трогаются.
