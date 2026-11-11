## MODIFIED Requirements

### Requirement: Статус компонента — из сработавших критических алертов

Статус компонента SHALL выводиться из алертов Prometheus в состоянии `firing` по правилам `infra/observability/alerts.yml`: `outage` — компонент недоступен (контейнер не запущен, проба не проходит, нет потребителей очереди), `degraded` — работает со сбоями (перезапуски, упал один из экземпляров воркера, всплеск 5xx у API, сбои прогонов, задачи стоят), иначе — `operational`. Алерты сервисов, без которых компоненты работают, и `ReviewDlqNotEmpty` SHALL NOT влиять на статус.

#### Scenario: Воркер остановлен

- **WHEN** горит `ContainerDown` с `container_name = dmc268-worker` или `dmc268-worker-<N>`
- **THEN** `worker` — `degraded`: задачи забирают остальные экземпляры; `api` и `queue` — `operational`

#### Scenario: Все воркеры остановлены

- **WHEN** у очереди ревью нет потребителей, и горит `WorkerNotConsuming`
- **THEN** `worker` — `outage`, даже если `ContainerDown` по экземплярам ещё не загорелся

#### Scenario: Всплеск 5xx у API

- **WHEN** горит `Api5xxSpike`
- **THEN** `api` — `degraded`

#### Scenario: Воркер подписан, но стоит

- **WHEN** горит `WorkerStalled`
- **THEN** `worker` в `degraded`, как и когда горит только `ReviewJobStuck`: по метрикам медленную модель от зависания не отличить

#### Scenario: Алерт другого сервиса

- **WHEN** горит только `DiskSpaceCritical` или `ContainerDown` реплики базы
- **THEN** все компоненты — `operational`

#### Scenario: Сообщение в DLQ

- **WHEN** горит только `ReviewDlqNotEmpty`
- **THEN** все компоненты — `operational`: у DLQ нет срока жизни сообщений, и одно сообщение
  держало бы `degraded` до ручного разбора, а падающие прогоны ловит `ReviewRunsFailing`
