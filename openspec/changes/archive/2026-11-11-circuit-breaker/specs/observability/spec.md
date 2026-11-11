## ADDED Requirements

### Requirement: Состояние breaker'а провайдера

Воркер SHALL отдавать gauge `review_circuit_breaker_state` с меткой `provider` (`GitHub`, `Ollama`,
`Eurorouter`): 0 — замкнут, 1 — пауза вышла и ждёт пробы, 2 — разомкнут. Breaker не замкнут 15
минут при задачах в `review_jobs.qq` — SHALL гореть `ProviderCircuitOpen` (critical) с меткой
провайдера; без задач SHALL NOT.

#### Scenario: Долгий отказ GitHub

- **WHEN** breaker GitHub 15 минут разомкнут или ждёт пробы, а в очереди задачи
- **THEN** горит `ProviderCircuitOpen` с `provider="GitHub"`

#### Scenario: Ночной сбой без трафика

- **WHEN** breaker ждёт пробы, а очередь пуста
- **THEN** `ProviderCircuitOpen` молчит

## MODIFIED Requirements

### Requirement: Алерты зависшего воркера и памяти контейнера

Алерт SHALL гореть, когда у `review_jobs.qq` есть потребитель и готовые задачи, но 15 минут нет
подтверждений (`WorkerStalled`); когда сообщение выдано и 20 минут не подтверждено
(`ReviewJobStuck`); когда память контейнера `dmc268-*` 10 минут выше 90 % лимита
(`ContainerMemoryNearLimit`). Поток коротких прогонов, очередь без потребителя и не замкнутый
breaker провайдера SHALL NOT зажигать первые два.

#### Scenario: Воркер завис на задаче

- **WHEN** сообщение выдано воркеру 20 минут назад, за ним копится очередь, подтверждений нет
- **THEN** горят `ReviewJobStuck` и `WorkerStalled`, а `WorkerNotConsuming` молчит

#### Scenario: Прогоны идут подряд

- **WHEN** очередь не пустеет и сообщение в работе всегда, но задачи подтверждаются
- **THEN** ни `WorkerStalled`, ни `ReviewJobStuck` не горят

#### Scenario: Потребителя нет

- **WHEN** у `review_jobs.qq` нет потребителя, а готовые задачи лежат
- **THEN** горит только `WorkerNotConsuming`

#### Scenario: Рестарт брокера

- **WHEN** брокер перезапущен и счётчик подтверждений начался с нуля, а воркер так и не подтвердил задачу
- **THEN** `WorkerStalled` продолжает гореть

#### Scenario: Провайдер на паузе

- **WHEN** breaker провайдера не замкнут, прогоны откладываются, и 20 минут нет подтверждений
- **THEN** `WorkerStalled` и `ReviewJobStuck` молчат, горит `ProviderCircuitOpen`

#### Scenario: Память у потолка

- **WHEN** контейнер `dmc268-worker` 10 минут занимает больше 90 % лимита памяти
- **THEN** горит предупреждение `ContainerMemoryNearLimit` с `container_name="dmc268-worker"` и `service="worker"`
- **AND** контейнер с лимитом 0 алерт не зажигает
