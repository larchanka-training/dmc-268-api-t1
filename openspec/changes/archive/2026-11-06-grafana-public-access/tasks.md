## 1. Инфраструктура

- [x] 1.1 Сеть `dmc268-grafana-proxy` (`internal`, без флага стека) в `infra/network.tf`
- [x] 1.2 Grafana в `infra/observability.tf`: сеть `dmc268-grafana-proxy`, env `GF_SERVER_ROOT_URL` из `public_origin` на порту `8443`, `GF_SERVER_SERVE_FROM_SUB_PATH=true`, `GF_SECURITY_COOKIE_SECURE=true`
- [x] 1.3 Grafana: `GF_METRICS_ENABLED=false` и `GF_AUTH_BASIC_ENABLED=false` (замечания ревью PR #372)
- [x] 1.4 Проверка образа Grafana 13.2.4 с этим env запуском: подпуть, редирект корня, `Secure` у cookie, `401` без сессии, CSRF за прокси, `/grafana/metrics` уводит на вход, basic auth — `401` без блокировки логина

## 2. Тесты

- [x] 2.1 `infra/tests/observability.tftest.hcl`: env Grafana (включая выключенные `/metrics` и basic auth), членство в сети, сеть без стека
- [x] 2.2 `tests/test_infra_isolation.py`, `tests/infrastructure/test_observability_stack.py`: сети Grafana и `internal` у новой сети

## 3. Документация

- [x] 3.1 `infra/README.md`: таблица сетей, «Стек наблюдаемости» — вход через `:8443/grafana/`, учётки, переименование `admin`, адрес туннеля, порядок отката (сначала фронт)
- [x] 3.2 `docs/SYSTEM_DESIGN.md`: узел Grafana и служебные сети
