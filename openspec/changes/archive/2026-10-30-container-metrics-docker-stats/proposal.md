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
- Метрики контейнеров снимает receiver `docker_stats` через тот же socket-proxy, в отдельном
  экземпляре коллектора `dmc268-otelcol-docker` (тот же образ, свой конфиг
  `otelcol-docker.yaml`, job Prometheus `docker-stats`). Inspect через proxy отдаёт Env всех
  контейнеров, а основной коллектор разбирает OTLP из сети данных и из интернета (#234): в сети
  proxy стоят только proxy и новый экземпляр, входящего OTLP у него нет. Без proxy не
  поднимается только он, трейсы и логи идут через основной коллектор. id, hostname и образ
  контейнера удаляются из ресурса: меняются каждым деплоем.
- Белый список socket-proxy: плюс `/containers/{id}/stats` и `/events` (без событий новые
  контейнеры не находятся — проверено запуском через шаблон).
- Панели дашборда — на `container_cpu_usage_nanoseconds_total` и
  `container_memory_usage_total_bytes` по `container_name`.
- `infra/README.md`: раздел «Метрики контейнеров» — почему не cAdvisor и почему отдельный
  экземпляр; таблица памяти без cAdvisor, с экземпляром 96 МБ (стек 3424 МБ, стенд 6752 МБ);
  ротация пароля Grafana — через stdin: командная строка `docker exec` видна в `/events`.

## Capabilities

### New Capabilities

Нет.

### Modified Capabilities

- `observability`: стек из восьми контейнеров; метрики контейнеров — receiver `docker_stats`
  отдельного экземпляра коллектора; Docker API — только у него.

## Impact

- `infra/observability.tf`, `infra/locals.tf`, `infra/variables.tf`, `infra/observability/*`,
  `docker-compose.yml`, тесты стека и изоляции сетей, `infra/tests/observability.tftest.hcl`,
  `infra/README.md`, `docs/BACKEND_ARCHITECTURE.md`, `docs/testing/TEST_PLAN.md`,
  `.github/workflows/ci.yml` (`otelcol validate` нового конфига).
- Деплой удаляет `dmc268-cadvisor`, создаёт `dmc268-otelcol-docker` и пересоздаёт socket-proxy,
  Prometheus и Grafana (сменились их конфиги): короткая пауза в метриках и в Grafana. Основной
  коллектор, приём трейсов и логов, база и брокер не трогаются.
