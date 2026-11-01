# Tasks

## 1. Правила и доставка

- [x] 1.1 `infra/observability/alerts.yml`: правила по каждому сигналу #100 и DLQ, пороги и уровни
- [x] 1.2 `alerts.test.yml`: «горит / молчит» на каждый алерт, `promtool test rules` в CI
- [x] 1.3 `alertmanager.yml`: Telegram с `bot_token_file` и `chat_id_file`, маршруты по `severity`, подавление предупреждений критическим алертом того же сервиса
- [x] 1.4 `prometheus.yml`: `rule_files`, `alerting`, job `alertmanager`, `blackbox-services`, `blackbox-public`

## 2. Источники сигналов

- [x] 2.1 `docker_stats`: `container.uptime` и `container.restarts`
- [x] 2.2 blackbox-exporter: `/health` API, TCP базы, реплики и брокера, HTTPS публичного входа со сроком сертификата
- [x] 2.3 `sqlquery/llm-tokens` в основном коллекторе и ревизия 0016 — `SELECT (created_at, tokens_used)` для `dmc268_monitor`
- [x] 2.4 `backup.sh`: отметка об успешной копии в textfile node-exporter, том `dmc268-backup-metrics`

## 3. Развёртывание

- [x] 3.1 `infra/observability.tf`: контейнеры `dmc268-alertmanager`, `dmc268-blackbox`, сеть `dmc268-alerting`, том `dmc268-alertmanager-data`, правила и цель публичного входа в Prometheus
- [x] 3.2 Переменные `alert_telegram_bot_token`, `alert_telegram_chat_id`: необязательные, с проверкой формата; `deploy.yml` передаёт их из секретов
- [x] 3.3 `docker-compose.yml`: те же сервисы и конфиги, секреты из окружения
- [x] 3.4 CI: `amtool check-config`, `blackbox_exporter --config.check`, `promtool check config` с правилами
- [x] 3.5 `infra/README.md`: раздел «Алерты» с порогами, секреты, память, проверка доставки
- [x] 3.6 Тесты: `tests/infrastructure/test_alerting.py`, `infra/tests/observability.tftest.hcl`, сети в `tests/test_infra_isolation.py`, ревизия 0016

## 4. Проверка на стенде

- [ ] 4.1 Деплой без секретов Telegram зелёный, повторный `plan` — `No changes`, тестовый алерт виден в `amtool alert query`
- [ ] 4.2 `/api/v1/rules` Prometheus стенда отдаёт все группы `alerts.yml`, `/api/v1/targets` — цели blackbox в `up`, ряд `review_llm_tokens_month` и `dmc268_backup_last_success_timestamp_seconds` есть
- [ ] 4.3 Секреты `TELEGRAM_BOT_TOKEN_DMC268_T1` и `TELEGRAM_CHAT_ID_DMC268_T1` заведены, следующий деплой пересоздал Alertmanager
- [ ] 4.4 Тестовый алерт (`amtool alert add`, `infra/README.md`) пришёл в канал
- [ ] 4.5 Синтетическое срабатывание правила доходит в канал: остановить `dmc268-worker` на 10 минут — приходят `ContainerDown` и `WorkerNotConsuming`, после старта — `RESOLVED`
