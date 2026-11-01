## Why

Стенд — prod-окружение (#97), а алертов у него нет совсем: в `prometheus.yml` нет ни `rule_files`, ни `alerting`, `/api/v1/rules` отдаёт пустой список (аудит наблюдаемости 30.10, #100). Упавший воркер, полная DLQ, отвалившаяся реплика, несвежая копия базы, кончающийся диск или сертификат сейчас видно только тому, кто сам открыл Grafana. Задача #100: критические алерты по SLA с доставкой в Telegram.

## What Changes

- Правила алертов `infra/observability/alerts.yml` в Prometheus (`rule_files`) и тесты к ним `alerts.test.yml` (`promtool test rules`): падение и перезапуски контейнеров, сервис не отвечает на проверку, публичный вход недоступен, воркер не забирает задачи, всплеск 5xx у API, доля упавших прогонов, DLQ не пуста, бюджет токенов модели, диск (свободное место и прогноз заполнения), реплика без потока и с отставанием, несвежая копия базы, срок сертификата, недоступная цель скрейпа.
- Alertmanager (`dmc268-alertmanager`) — доставка в Telegram. Токен бота и chat id — файлами (`bot_token_file`, `chat_id_file`): на стенде из новых секретов `TELEGRAM_BOT_TOKEN_DMC268_T1` и `TELEGRAM_CHAT_ID_DMC268_T1`, локально из окружения. Без секретов алерты считаются, но не доставляются; битый формат роняет `plan`.
- blackbox-exporter (`dmc268-blackbox`) — пробы `/health` API и портов базы, реплики, брокера (статус healthcheck контейнера в `docker_stats` 0.158 недоступен) и HTTPS публичного входа со сроком сертификата.
- Своя не-internal сеть `dmc268-alerting`: наружу из стека ходят только Alertmanager и blackbox-exporter, портов на хосте у них нет.
- Receiver `docker_stats` отдаёт `container.uptime` и `container.restarts`.
- Расход токенов с начала месяца — receiver `sqlquery` основного коллектора по `review_runs.tokens_used`; миграция 0016 даёт роли `dmc268_monitor` `SELECT` только на `created_at` и `tokens_used`.
- `backup.sh` после успешной копии пишет отметку времени для textfile node-exporter (новый том `dmc268-backup-metrics`).
- CI: `promtool test rules`, `amtool check-config`, проверка конфига blackbox в job `observability-configs`.

## Capabilities

### New Capabilities

### Modified Capabilities

- `observability`: стек получает Alertmanager и blackbox-exporter; критические алерты с доставкой в Telegram; расход токенов модели для алерта бюджета.

## Impact

- `infra/observability/` (новые `alerts.yml`, `alerts.test.yml`, `alertmanager.yml`, `blackbox.yml`, `public-targets.yml`; правки `prometheus.yml`, `otelcol.yaml`, `otelcol-docker.yaml`), `infra/observability.tf`, `infra/replica.tf`, `infra/locals.tf`, `infra/variables.tf`, `infra/postgres/backup.sh`, `infra/README.md`, `infra/tests/observability.tftest.hcl`, `docker-compose.yml`.
- `alembic/versions/0016_grant_monitor_llm_tokens.py`.
- `.github/workflows/ci.yml`, `.github/workflows/deploy.yml`.
- Настройки репозитория: секреты `TELEGRAM_BOT_TOKEN_DMC268_T1` и `TELEGRAM_CHAT_ID_DMC268_T1` необязательны — без них деплой проходит, а доставка включается их появлением и следующим деплоем.
- Память на сервере: +128 МБ лимитов (Alertmanager и blackbox-exporter по 64).
- Первый деплой пересоздаёт `prometheus`, `node-exporter`, `otelcol`, `otelcol-docker` (новые конфиги) и `postgres-backup` (новый том); данные в томах сохраняются.
- Код приложения не меняется.
