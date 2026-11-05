## 1. Инфраструктура

- [x] 1.1 Сеть `dmc268-grafana-proxy` (`internal`, без флага стека) в `infra/network.tf`
- [x] 1.2 Grafana в `infra/observability.tf`: сеть `dmc268-grafana-proxy`, env `GF_SERVER_ROOT_URL` из `public_origin`, `GF_SERVER_SERVE_FROM_SUB_PATH=true`, `GF_SECURITY_COOKIE_SECURE=true`
- [x] 1.3 Проверка образа Grafana 13.2.4 с этим env запуском: подпуть, редирект корня, `Secure` у cookie, `401` без сессии, CSRF за прокси

## 2. Тесты

- [x] 2.1 `infra/tests/observability.tftest.hcl`: env Grafana, членство в сети, сеть без стека
- [x] 2.2 `tests/test_infra_isolation.py`, `tests/infrastructure/test_observability_stack.py`: сети Grafana и `internal` у новой сети

## 3. Документация

- [x] 3.1 `infra/README.md`: таблица сетей, «Стек наблюдаемости» — вход через `/grafana/`, учётки, адрес туннеля
- [x] 3.2 `docs/SYSTEM_DESIGN.md`: узел Grafana и служебные сети
