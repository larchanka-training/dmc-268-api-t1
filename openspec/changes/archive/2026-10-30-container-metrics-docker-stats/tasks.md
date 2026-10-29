# Tasks

## 1. docker_stats вместо cAdvisor

- [x] 1.1 Receiver `docker_stats` (`tcp://dockerproxy:2375`) в конвейере метрик коллектора, лишние атрибуты ресурса — вон
- [x] 1.2 Коллектор в сети socket-proxy (compose и infra), cAdvisor убран: контейнер, образ, потолок памяти, job Prometheus
- [x] 1.3 Белый список socket-proxy: `/containers/{id}/stats` и `/events` — проверено запуском коллектора через шаблон
- [x] 1.4 Панели «CPU контейнеров» и «Память контейнеров» на метриках `docker_stats`

## 2. Тесты, документы, приёмка

- [x] 2.1 Тесты стека, изоляции сетей и `infra/tests/observability.tftest.hcl`
- [x] 2.2 `infra/README.md` («Метрики контейнеров», память), `docs/BACKEND_ARCHITECTURE.md`, `docs/testing/TEST_PLAN.md`
- [x] 2.3 ruff, lint-imports, mypy, pytest с `PYTEST_FAIL_ON_SKIP=1`, openspec validate; `tofu test` — в CI
