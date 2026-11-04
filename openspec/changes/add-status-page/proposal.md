## Why

Пользователю кабинета и гостю сейчас негде увидеть, работает ли сервис: упавший воркер выглядит
как «ревью почему-то не пришло», а лежащий API — как сломанный интерфейс. Задача #299 —
публичная страница статуса. Источник правды для неё уже есть: критические алерты #100
(`infra/observability/alerts.yml`) будят команду по тем же сигналам, по которым страница должна
показывать сбой. Своя проверка на стороне страницы разошлась бы с алертами — страница говорила
бы «всё работает», пока команде приходит `ContainerDown`.

## What Changes

- Публичная ручка `GET /api/v1/status` без сессии: сводный статус и статусы компонентов `api`,
  `worker`, `queue` (`operational`, `degraded`, `outage`, `unknown`) и время проверки
  `checked_at`. Модель ответа — `ServiceStatusResponse`, статусы — enum в контракте.
- Статус — из сработавших алертов Prometheus (`ALERTS{alertstate="firing"}`): таблица «алерт →
  компонент и статус» — чистая функция домена. Алерт о слепоте источника, на котором держатся
  алерты компонента (`ScrapeTargetDown` нужного job, `ContainerMetricsMissing`), делает компонент
  `unknown`, а не `operational`.
- Prometheus не задан, недоступен, не ответил за 2 с, ответил не `2xx` или не по формату — все
  компоненты `unknown`, ответ `200`, в логе API предупреждение. Наружу не уходят ни имена
  алертов, ни метки, ни адреса, ни причины сбоя.
- Оценка кэшируется в процессе API на 15 с: публичная ручка не превращает поток запросов в
  поток запросов к Prometheus.
- Настройка `PROMETHEUS_URL` (по умолчанию пустая). На стенде её получает API при
  `observability_enabled` — `http://dmc268-prometheus:9090` по сети данных, где Prometheus уже
  стоит (#324); в docker compose — `http://prometheus:9090`.
- Граница «телеметрия не источник данных продукта» получает одно явное исключение — эту ручку.

## Capabilities

### New Capabilities

- `service-status`: публичный статус сервиса из сработавших критических алертов.

### Modified Capabilities

- `observability`: требование «Телеметрия не источник данных продукта» допускает статус сервиса
  из алертов Prometheus — только статусы компонентов.

## Impact

- Код: `app/domain/service_status.py`, `app/domain/enums.py` (`ServiceStatus`,
  `ServiceComponent`), порт `app/application/ports/alert_source.py`, кэш оценки
  `app/application/use_cases/service_status.py`, адаптер
  `app/infrastructure/monitoring/prometheus.py`, `app/infrastructure/container.py`,
  `app/api/dependencies.py`, роут `app/api/status.py`, `app/api/factory.py`, `app/config.py`.
- Контракт: новая операция `getServiceStatus`, схемы `ServiceStatusResponse`,
  `ComponentStatusResponse`, `ServiceStatus`, `ServiceComponent` в `docs/openapi/openapi.json`;
  фронтенд генерирует по ним типы.
- Развёртывание: `infra/locals.tf` (`status_env`), `infra/app.tf`, `docker-compose.yml`,
  `.env.example`; тесты `infra/tests/observability.tftest.hcl`. Новая переменная в env API
  пересоздаёт его контейнер на первом деплое — как любой деплой с новым образом.
- Документация: `README.md`, `infra/README.md`, `docs/BACKEND_ARCHITECTURE.md`,
  `.agents/rules/backend.md`.
- Ветка отведена от `feat/critical-alerts` (#324): алерты, пробы blackbox и Prometheus в сети
  данных приходят оттуда.
- Без миграций; база и брокер не трогаются.
