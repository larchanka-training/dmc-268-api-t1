## Why

Grafana стенда открывается только SSH-туннелем на `127.0.0.1:3000`: чтобы посмотреть дашборд или
трейс, каждому в команде нужен SSH-доступ к серверу. Публичный вход стенда (`https://<адрес>`,
nginx интерфейса `dmc268-ui`) уже есть и терминирует TLS, поэтому Grafana можно отдавать через
него по подпути `/grafana/` — по личным учёткам, без доступа к серверу. Отдаётся она на отдельном
порту `8443` того же хоста: свой origin, и XSS в Grafana не прочитает API кабинета.

## What Changes

- Grafana стенда получает `GF_SERVER_ROOT_URL` = `public_origin` + `:8443/grafana/` (без
  `public_origin` — относительный `/grafana/`),
  `GF_SERVER_SERVE_FROM_SUB_PATH=true` и `GF_SECURITY_COOKIE_SECURE=true`. Анонимный вход и
  регистрация остаются выключенными.
- Подпуть открыт наружу целиком, поэтому выключаются `GF_METRICS_ENABLED` (`/grafana/metrics`
  отдавал без входа версию, коммит и число пользователей; Prometheus стенда Grafana не опрашивает)
  и `GF_AUTH_BASIC_ENABLED` (basic auth шёл мимо `limit_req` на `/grafana/login` и позволял
  держать `admin` заблокированным; API-клиентов у Grafana нет).
- Новая `internal`-сеть `dmc268-grafana-proxy`: в ней только Grafana и интерфейс, nginx которого
  на порту `8443` проксирует `location /grafana/` на `dmc268-grafana:3000`. Сеть создаётся и при выключенном
  стеке, как `dmc268-otlp`: стек интерфейса подключается к ней по имени через `data "docker_network"`.
- Туннель на петле продолжает работать, но адрес Grafana в нём —
  `http://localhost:3000/grafana/`: с подпутём Grafana отвечает только под ним, а корень
  перенаправляет на публичный адрес.
- Локальный compose не меняется: публичного входа там нет, Grafana остаётся в корне без логина.
- Половина интерфейса (`server` на `8443` с `location /grafana/` в nginx, подключение к сети) — в `dmc-268-ui-t1`.

## Capabilities

### New Capabilities

Нет.

### Modified Capabilities

- `observability`: новые требования «Grafana стенда — через публичный вход по учётке», «Подпуть
  Grafana не открывает лишнего наружу» и «Сеть прокси Grafana».

## Impact

- `infra/network.tf`, `infra/observability.tf`, `infra/variables.tf`, `infra/tests/observability.tftest.hcl`,
  `tests/test_infra_isolation.py`, `tests/infrastructure/test_observability_stack.py`,
  `infra/README.md`, `docs/SYSTEM_DESIGN.md`.
- Деплой создаёт сеть и пересоздаёт только `dmc268-grafana` (новый env и сеть). Тома, база и
  брокер не затрагиваются; данные Grafana на томе, дашборды и datasource провижнятся из файлов.
- Проверка на стенде после выката обеих половин (план создаёт сеть и заменяет только
  `dmc268-grafana`; `https://<адрес>:8443/grafana/` и туннель открывают форму входа) — в описании
  пул-реквеста, а не в задачах: change архивируется до мерджа.
- Grafana становится доступна из интернета: защита — только её логин. Пароль админа в команде не
  раздаётся, логин `admin` на стенде один раз переименовывается, затем участникам заводятся
  личные учётки.
