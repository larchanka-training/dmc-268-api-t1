# service-status Specification

## Purpose
Определяет публичный статус сервиса для страницы статуса: какие компоненты он показывает, как
статус каждого выводится из сработавших критических алертов, что отдаётся, когда мониторинг не
отвечает или слеп, и чего ответ не раскрывает.

## Requirements

### Requirement: Статус сервиса открыт без сессии

`GET /api/v1/status` SHALL отвечать `200` без cookie сессии и без проверки `Origin`. Ответ SHALL
содержать сводный `status`, массив `components` из ровно трёх элементов `api`, `worker`, `queue`
в этом порядке, у каждого — `id` и `status`, и время оценки `checked_at` в UTC с `Z`. Статусы
SHALL быть значениями `operational`, `degraded`, `outage`, `unknown` и SHALL быть описаны в
схеме API перечислениями.

#### Scenario: Гость открывает страницу статуса

- **WHEN** `GET /api/v1/status` вызывается без cookie сессии
- **THEN** ответ — `200` с `status`, тремя компонентами `api`, `worker`, `queue` по порядку и
  `checked_at`

#### Scenario: Схема описывает статусы перечислением

- **WHEN** фронтенд генерирует типы из `docs/openapi/openapi.json`
- **THEN** у `ServiceStatusResponse` поля `status` и `components[].status` — перечисление из
  четырёх статусов, `components[].id` — перечисление из трёх компонентов

### Requirement: Статус компонента — из сработавших критических алертов

Статус компонента SHALL выводиться из алертов Prometheus в состоянии `firing` по правилам
`infra/observability/alerts.yml`: `outage` — компонент недоступен (контейнер не запущен, проба
не проходит, нет потребителей очереди), `degraded` — работает со сбоями (перезапуски, всплеск
5xx у API, сбои прогонов, задачи стоят), иначе — `operational`. Алерты о сервисах, без которых
компоненты работают (реплика и копии базы, интерфейс, диск, бюджет модели), и
`ReviewDlqNotEmpty` SHALL NOT влиять на статус.

#### Scenario: Воркер остановлен

- **WHEN** горит `ContainerDown` с `container_name = dmc268-worker`
- **THEN** `worker` — `outage`, `api` и `queue` — `operational`

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

### Requirement: Недоступная база — outage у api и worker

Своего компонента у базы нет, но без неё API отвечает ошибкой, хотя `/health` проходит, а
воркер не работает. Когда контейнер `dmc268-postgres` не запущен или проба его порта не
проходит, `api` и `worker` SHALL быть `outage`.

#### Scenario: База недоступна

- **WHEN** горит `ContainerDown` с `container_name = dmc268-postgres` или `ServiceProbeFailed` с
  `service = postgres`
- **THEN** `api` и `worker` — `outage`, `queue` — `operational`

### Requirement: Слепой мониторинг не выдаётся за исправный

Компонент SHALL быть `unknown`, а не `operational`, когда горит алерт о слепоте источника, на
котором держатся его алерты: Prometheus не снимает цель скрейпа этих сигналов
(`ScrapeTargetDown` нужного job) или метрик контейнеров нет вовсе (`ContainerMetricsMissing`).

#### Scenario: Не снимаются метрики брокера

- **WHEN** горит `ScrapeTargetDown` с `job = rabbitmq`
- **THEN** `worker` — `unknown`: молчание `WorkerNotConsuming` ничего не значит

#### Scenario: Лежит экспортёр метрик контейнеров

- **WHEN** горит `ScrapeTargetDown` с `job = docker-stats`
- **THEN** все три компонента — `unknown`

### Requirement: Сводный статус — худший из компонентов

Статус компонента при нескольких сработавших алертах и сводный статус сервиса SHALL быть
тяжелейшим по порядку `outage` > `degraded` > `unknown` > `operational`.

#### Scenario: Сбой и слепота у одного компонента

- **WHEN** у `worker` горит `ReviewRunsFailing`, а метрики брокера не снимаются
- **THEN** `worker` — `degraded`

#### Scenario: Компоненты в разных состояниях

- **WHEN** `api` — `operational`, `worker` — `unknown`, `queue` — `degraded`
- **THEN** сводный `status` — `degraded`

### Requirement: Недоступный мониторинг — unknown, а не ошибка

Когда адрес Prometheus не задан, Prometheus не ответил за 2 секунды, ответил не `2xx` или телом
не по формату, ручка SHALL отвечать `200` со всеми компонентами и сводным статусом `unknown` и
SHALL писать в лог API предупреждение. Без адреса запросов к Prometheus SHALL NOT быть.

#### Scenario: Prometheus недоступен

- **WHEN** запрос к Prometheus падает по тайм-ауту
- **THEN** ответ — `200`, все статусы `unknown`, в логе API предупреждение

#### Scenario: Адрес не задан

- **WHEN** `PROMETHEUS_URL` пуст
- **THEN** ответ — `200`, все статусы `unknown`, запроса к Prometheus нет

### Requirement: Ответ не раскрывает устройство стенда

Ответ SHALL содержать только статусы и время оценки. Имена алертов, их метки, адреса и имена
контейнеров и причины сбоя мониторинга SHALL NOT попадать в ответ.

#### Scenario: Горит алерт с метками

- **WHEN** горит `ContainerDown` с `container_name` и `instance`
- **THEN** в теле ответа нет ни имени алерта, ни значений его меток

### Requirement: Оценка статуса кэшируется

API SHALL кэшировать оценку в памяти процесса на 15 секунд и SHALL ходить в Prometheus не чаще
одного раза за этот срок, сколько бы запросов ни пришло. `checked_at` SHALL быть временем оценки,
а не временем ответа. Неудачная оценка (`unknown`) SHALL кэшироваться так же. Пока один запрос
обновляет устаревшую оценку, остальные SHALL NOT его ждать и SHALL получать прошлую оценку;
ждут только запросы до первой оценки процесса.

#### Scenario: Поток запросов к странице

- **WHEN** за 15 секунд пришло много запросов `GET /api/v1/status`
- **THEN** в Prometheus ушёл один запрос, и у всех ответов одинаковый `checked_at`

#### Scenario: Кэш устарел

- **WHEN** с прошлой оценки прошло 15 секунд
- **THEN** следующий запрос оценивает статус заново с новым `checked_at`

#### Scenario: Prometheus отвечает медленно

- **WHEN** оценка устарела, один запрос уже ждёт ответа Prometheus, и приходит ещё запрос
- **THEN** второй запрос сразу получает прошлую оценку с её `checked_at` — старше 15 секунд на
  время идущей проверки
