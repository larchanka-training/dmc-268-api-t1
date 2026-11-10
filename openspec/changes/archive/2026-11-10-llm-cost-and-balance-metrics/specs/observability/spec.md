## ADDED Requirements

### Requirement: Расход модели в евро и его бюджет

Стоимость вызовов модели SHALL считаться по `review_runs.cost_eur` прогонов с начала месяца (UTC)
и попадать в Prometheus рядом `review_llm_cost_eur_month` через коллектор, под ролью мониторинга,
которой из `review_runs` доступны только `created_at`, `tokens_used` и `cost_eur`. Бюджет — 20 € в
месяц: выше 80 % SHALL гореть `LlmCostBudgetNearLimit` (warning), с 100 % — `LlmCostBudgetExceeded`
(critical). «Обзор» SHALL показывать расход рядом с бюджетом.

#### Scenario: Расход приближается к бюджету

- **WHEN** прогоны с начала месяца стоили 17 €
- **THEN** горит `LlmCostBudgetNearLimit`, а `LlmCostBudgetExceeded` молчит

#### Scenario: Бюджет исчерпан

- **WHEN** прогоны с начала месяца стоили 20 €
- **THEN** горит `LlmCostBudgetExceeded`

#### Scenario: Ровно 80 %

- **WHEN** прогоны с начала месяца стоили 16 €
- **THEN** `LlmCostBudgetNearLimit` молчит

### Requirement: Баланс провайдера модели

Воркер, у которого Eurorouter основной или фоллбэк, SHALL отдавать gauge
`review_llm_provider_balance_eur` — `data.total_credits − data.total_usage` из `GET /credits` первым ключом,
не чаще раза в 5 минут. Сбой опроса (сеть, не `200`, тело не того вида) SHALL давать одно
предупреждение без ключа и тела ответа и ни одной точки до следующего опроса; исключение наружу
SHALL NOT выходить. Ниже 5 € SHALL гореть `LlmProviderBalanceLow` (warning), ниже 1 € —
`LlmProviderBalanceCritical` (critical).

#### Scenario: Остаток

- **WHEN** `/credits` отвечает `data.total_credits` `"20.0000000000"` и `data.total_usage` `"6.9039961619"`
- **THEN** gauge равен 13,0960038381

#### Scenario: Опрос не чаще раза в 5 минут

- **WHEN** метрики собираются раз в минуту
- **THEN** запрос к `/credits` уходит не чаще раза в 5 минут, между опросами отдаётся прошлый результат

#### Scenario: Провайдер недоступен

- **WHEN** `/credits` не отвечает или отвечает не `200`
- **THEN** точки gauge нет, в логе одно предупреждение без ключа и тела ответа, воркер работает дальше

#### Scenario: Остаток на исходе

- **WHEN** остаток 0,99 €
- **THEN** горят `LlmProviderBalanceLow` и `LlmProviderBalanceCritical`

#### Scenario: Без Eurorouter

- **WHEN** воркер работает с Ollama без фоллбэка или с заглушкой
- **THEN** gauge нет, и к `/credits` запросов нет

### Requirement: Идентификатор запроса провайдера модели

Каждый ответ провайдера модели с кодом не `200` SHALL давать одну запись лога с кодом и
`x-request-id` ответа в поле `review.llm.request_id`, если заголовок есть. Текст ответа и ключ
SHALL NOT попадать в запись. Спан httpx ответа с `x-request-id`, успешного или нет, SHALL нести
атрибут `review.llm.request_id`.

#### Scenario: Отказ провайдера

- **WHEN** Eurorouter отвечает `402` с `x-request-id` `req-402` и текстом ошибки
- **THEN** в логе запись с `HTTP 402` и `review.llm.request_id` = `req-402`, текста ответа в ней нет

#### Scenario: Успешный ответ

- **WHEN** ответ провайдера несёт `x-request-id`
- **THEN** у спана httpx этого запроса атрибут `review.llm.request_id` равен заголовку

### Requirement: Провайдеру модели не уходит контекст трейса

Запрос к провайдеру модели SHALL NOT нести наш контекст трейса (`traceparent`, `trace_id`) и
другие метаданные сервиса: ни в заголовках, ни в теле. Запрос и ответ сопоставляются по
`x-request-id` провайдера.

#### Scenario: Заголовки запроса к модели

- **WHEN** воркер вызывает Eurorouter внутри спана этапа
- **THEN** в запросе нет `traceparent`, `tracestate` и `X-Trace-ID`, а в теле — только модель, сообщения, формат ответа и параметры генерации

## MODIFIED Requirements

### Requirement: Метрики сервиса — в Prometheus

Метрики SHALL собираться OTel SDK и уходить через OTLP в коллектор и Prometheus; ручка
`/metrics` и `prometheus_client` SHALL NOT использоваться. Экспорт SHALL включаться явно: без
настройки метрики никуда не уходят и в лог ошибок нет. HTTP-метрики SHALL давать встроенная
телеметрия FastAPI. Ручные метрики SHALL ограничиваться бизнес-сигналами для алертов: прогоны по
статусам, длительность этапов, длительность каждого вызова провайдера модели с исходом,
пул соединений с базой, баланс провайдера.

#### Scenario: Без настройки

- **WHEN** сервис запущен без `OTEL_METRICS_EXPORTER`
- **THEN** метрики собираются, но никуда не уходят и в логе ошибок экспорта нет

#### Scenario: HTTP-метрики запросов

- **WHEN** запросы идут в API
- **THEN** в Prometheus есть метрики запросов по маршрутам и кодам ответов

#### Scenario: Бизнес-сигналы прогона

- **WHEN** прогон завершился со статусом `failed` после этапа `analysing`
- **THEN** счётчик прогонов по статусам вырос, длительность этапа записана в гистограмму этапов,
  а каждый фактический вызов провайдера модели на этом этапе — в гистограмму длительности вызова
  с исходом: метка провайдера стоит всегда, у успешного вызова рядом — модель ответа

#### Scenario: Метрики инфраструктуры

- **WHEN** Prometheus скрейпит стек
- **THEN** очередь отдаёт метрики плагином `rabbitmq_prometheus`, база — receiver `postgresql`
  коллектора (роль только-чтение `dmc268_monitor` с `pg_monitor`; собирать медленные запросы из
  `pg_stat_statements` не требуется — расширение остаётся для ручной диагностики), сервер —
  node_exporter, контейнеры — receiver `docker_stats` отдельного экземпляра коллектора через
  socket-proxy, с именем контейнера в лейбле `container_name`

#### Scenario: Ручки /metrics нет

- **WHEN** опрашиваются ручки API
- **THEN** ручки `/metrics` нет, а контракт API не изменился
