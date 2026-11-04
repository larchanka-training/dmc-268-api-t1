## 1. Домен и порт

- [x] 1.1 `ServiceStatus`, `ServiceComponent` в `app/domain/enums.py`
- [x] 1.2 Чистая функция `evaluate_service_status` и таблица `ALERT_RULES` в `app/domain/service_status.py`; тесты на каждую строку (и база для api и worker), слепоту, посторонние алерты (и DLQ), порядок тяжести и сверку с `alerts.yml`/`prometheus.yml`
- [x] 1.3 Порт `AlertSource` (`app/application/ports/alert_source.py`)
- [x] 1.4 Кэш оценки `ServiceStatusMonitor` (`app/application/use_cases/service_status.py`) с часами инъекцией, обновление без ожидания для остальных запросов; тесты TTL, кэша неудачи, прошлой оценки во время обновления и ожидания первой

## 2. Адаптер и проводка

- [x] 2.1 Адаптер `PrometheusAlertSource` (`app/infrastructure/monitoring/prometheus.py`); тесты на `httpx.MockTransport`: успех, тайм-аут, отказ соединения, 5xx, не JSON, не тот формат, пустой адрес
- [x] 2.2 Настройка `PROMETHEUS_URL`, монитор в контейнере, зависимость `get_service_status_monitor`, предупреждение на старте без адреса

## 3. Ручка и контракт

- [x] 3.1 Роут `GET /api/v1/status` (`app/api/status.py`), модель `ServiceStatusResponse`; тесты: гость без сессии — `200` и точная форма, недоступный источник — `unknown`, кэш между запросами, утечки нет
- [x] 3.2 `uv run python scripts/export_openapi.py`

## 4. Развёртывание и документация

- [x] 4.1 `local.status_env` и env API в `infra/`, тесты `infra/tests/observability.tftest.hcl`
- [x] 4.2 `docker-compose.yml`, `.env.example`
- [x] 4.3 `README.md`, `infra/README.md`, `docs/BACKEND_ARCHITECTURE.md`, `.agents/rules/backend.md`

## 5. Проверка на стенде

- [ ] 5.1 После деплоя `curl https://<стенд>/api/v1/status` отдаёт три компонента `operational`
- [ ] 5.2 Остановить `dmc268-worker` на 10 минут — `worker` и сводный статус `outage`; после старта — снова `operational`
