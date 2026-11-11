## ADDED Requirements

### Requirement: Алерты видят каждый экземпляр воркера

`ContainerDown` SHALL гореть по каждому экземпляру воркера, который должен работать, но не запущен; список экземпляров Prometheus SHALL получать от развёртывания. Экземпляр сверх списка SHALL NOT зажигать `ContainerDown`. `ContainerMemoryNearLimit` экземпляра SHALL нести `service` без номера экземпляра.

#### Scenario: Пропал второй экземпляр

- **WHEN** экземпляров два, и `dmc268-worker-2` не запущен дольше 2 минут
- **THEN** горит `ContainerDown` с `container_name="dmc268-worker-2"`

#### Scenario: Пропал первый экземпляр

- **WHEN** не запущен `dmc268-worker`
- **THEN** горит один `ContainerDown` с `container_name="dmc268-worker"`, а не два

#### Scenario: Память экземпляра у потолка

- **WHEN** `dmc268-worker-2` 10 минут занимает больше 90 % лимита памяти
- **THEN** горит `ContainerMemoryNearLimit` с `container_name="dmc268-worker-2"` и `service="worker"`
