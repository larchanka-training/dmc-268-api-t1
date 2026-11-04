## 1. Пороги и healthcheck

- [x] 1.1 `infra/rabbitmq/30-dmc268.conf` с абсолютными порогами памяти и диска, `upload` в `infra/rabbitmq.tf`, том в `docker-compose.yml`
- [x] 1.2 Healthcheck `check_port_connectivity` на стенде и в compose
- [x] 1.3 Проверка в podman на `rabbitmq:4.4-alpine` с `--memory 512m`: `status` показывает порог 0.3146 ГБ и диск 1.0 ГБ, `check_port_connectivity` проходит, `rabbitmq_prometheus` включён
- [x] 1.4 Тест: порог памяти ниже потолка контейнера (с порогом 600 МиБ падает)
- [x] 1.5 Комментарий в `infra/rabbitmq.tf`, абзац в `infra/README.md`

## 2. Выкат (после мёржа, вместе с PR #339 по #338)

- [ ] 2.1 Ручной запуск `deploy.yml` с `allow_stateful_replace=true` по tasks 2.x change `rabbitmq-fixed-hostname`
- [ ] 2.2 На стенде `rabbitmq-diagnostics status`: порог памяти 300 МиБ, диск 1 ГБ; контейнер `healthy`
- [ ] 2.3 Следующий деплой не трогает `docker_container.rabbitmq`
