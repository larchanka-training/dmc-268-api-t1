## Context

Мотивация — `proposal.md`, требования — дельты `specs/service-status/spec.md` и
`specs/observability/spec.md`. Что есть сейчас (с веткой `feat/critical-alerts`, #324):

- `infra/observability/alerts.yml` — правила #100: `ContainerDown` и `ContainerRestarting` по
  `container_name` (receiver `docker_stats`, job `docker-stats`), `ServiceProbeFailed` по метке
  цели `service` (blackbox, job `blackbox-services`), `WorkerNotConsuming` и `ReviewDlqNotEmpty`
  (метрики брокера, job `rabbitmq`), `Api5xxSpike` и `ReviewRunsFailing` (метрики приложения по
  OTLP через коллектор, job `collector`), `ScrapeTargetDown` (`up == 0`, метка правила
  `service: observability`), `ContainerMetricsMissing`.
- Prometheus (`dmc268-prometheus`) стоит в сети данных `dmc268-backend` — там же API. Порт 9090
  на хост не опубликован, авторизации нет; доступ — членством в сети.
- `evaluation_interval` не задан — Prometheus пересчитывает правила раз в минуту, скрейп — раз в
  15 с.
- Граница спеки `observability`: ответы API из телеметрии не строятся.

## Goals / Non-Goals

**Goals:**

- Страница показывает то же, что видит команда в алертах, без своих проверок.
- Решение — чистая функция с таблицей; ввод-вывод — адаптер; ошибка источника — `unknown`, не
  `5xx`.
- Публичная ручка не нагружает Prometheus и не раскрывает внутреннее устройство стенда.

**Non-Goals:**

- История инцидентов, uptime в процентах, подписка на уведомления.
- Отдельные компоненты для базы, реплики, интерфейса, бюджета модели: пользователю страницы
  важны три компонента, через которые проходит его ревью. Недоступную базу он видит как `outage`
  у `api` и `worker` (D2) — без неё они не работают.
- Общий для реплик кэш: у API одна реплика.

## Decisions

### D1. Источник — `ALERTS{alertstate="firing"}`, а не свои запросы к метрикам

Один instant-запрос к `/api/v1/query`. Пороги, `for:` и подавление шума уже выверены в правилах и
закреплены `promtool test rules`; повторять их выражениями на стороне API — второй источник
правды. Альтернатива — `/api/v1/alerts` Prometheus: отдаёт и `pending`, формат свой; ряд `ALERTS`
с фильтром по `alertstate` проще и тот же, что видит Alertmanager. Alertmanager как источник
отвергнут: в нём подавленные (`inhibit_rules`) алерты, и он не в сети данных.

### D2. Таблица `ALERT_RULES` в домене, сопоставление по имени и меткам

`app/domain/service_status.py`: строка таблицы — имя алерта, обязательные метки, компоненты и
статус. Компонент получает тяжелейший статус из сработавших строк, ни одной — `operational`.

| Компонент | outage | degraded | unknown (слепота) |
| --- | --- | --- | --- |
| api | `ContainerDown{container_name="dmc268-api"\|"dmc268-postgres"}`, `ServiceProbeFailed{service="api"\|"postgres"}` | `Api5xxSpike`, `ContainerRestarting{container_name="dmc268-api"}` | `ScrapeTargetDown{job="docker-stats"\|"blackbox-services"\|"collector"}`, `ContainerMetricsMissing` |
| worker | `ContainerDown{container_name="dmc268-worker"\|"dmc268-postgres"}`, `ServiceProbeFailed{service="postgres"}`, `WorkerNotConsuming` | `ContainerRestarting{container_name="dmc268-worker"}`, `ReviewRunsFailing` | `ScrapeTargetDown{job="docker-stats"\|"blackbox-services"\|"collector"\|"rabbitmq"}`, `ContainerMetricsMissing` |
| queue | `ContainerDown{container_name="dmc268-rabbitmq"}`, `ServiceProbeFailed{service="rabbitmq"}` | `ContainerRestarting{container_name="dmc268-rabbitmq"}` | `ScrapeTargetDown{job="docker-stats"\|"blackbox-services"}`, `ContainerMetricsMissing` |

База своего компонента не получает, но её алерты — в строках `api` и `worker`: `/health` API
базу не проверяет, и без строки базы страница говорила бы «всё работает», пока API отвечает 500
на всё остальное, а воркер падает. Поэтому и слепота `blackbox-services` (проба порта базы)
делает `worker` `unknown`. Реплика, копии и `ContainerRestarting` базы в таблицу не входят:
работу API и воркера они не останавливают.

`ReviewDlqNotEmpty` в таблице нет: у DLQ нет срока жизни сообщений, и одно ядовитое сообщение
держало бы `degraded` на публичной странице, пока DLQ не разберут руками. Падающие прогоны ловит
`ReviewRunsFailing`; DLQ остаётся алертом команды.

Слепота — по job скрейпа, а не по метке `service`: правило `ScrapeTargetDown` ставит
`service: observability`, и метка правила затирает метку цели. Ряды приложения несут
`job = dmc-268-api` (`honor_labels`), но `up` — всегда job скрейпа, поэтому в таблице
`collector`. Совпадение имён алертов, меток и job с `alerts.yml` и `prometheus.yml` сверяет
тест домена; там же проверка, что сопоставляемые метки не задаёт само правило.

### D3. Один порядок тяжести: outage > degraded > unknown > operational

И для компонента, и для сводного статуса. Сработавший алерт — подтверждённая проблема, слепота —
только отсутствие подтверждения. Поэтому `degraded` при частично слепых источниках остаётся
`degraded`, а `outage` бьёт всё. Вариант «слепота сильнее degraded» отвергнут: он прятал бы
известный сбой за «неизвестно». Сводный статус `unknown` — только когда ни у одного компонента
нет подтверждённой проблемы, но хотя бы один не проверен.

### D4. Порт `AlertSource`, адаптер возвращает `None` на любой сбой

`firing_alerts() -> tuple[FiringAlert, ...] | None`. Порт — ради границы слоя (httpx запрещён в
`application`) и подмены в тестах. Исключения наружу не идут: для страницы недоступный
Prometheus — штатный исход `unknown`, и роуту незачем знать типы ошибок httpx. Тайм-аут —
`httpx.Timeout(2.0)` на фазу; пустой `PROMETHEUS_URL` — адаптер без клиента, запроса нет. Тело
разбирают строгие приватные модели pydantic; ошибка разбора — `None`. В лог — тип исключения или
HTTP-статус, без тела ответа.

### D5. Кэш оценки — `ServiceStatusMonitor` в слое приложения, а не `CacheStore`

Кэшируется оценка целиком вместе с `checked_at`: время в ответе — когда сервис проверял.
`CacheStore` хранит строки — понадобился бы кодек отчёта ради одной записи. Монитор — поле
`TTL` 15 с, часы `monotonic` и `now` инъекцией. Обновление — под `threading.Lock`, взятым без
ожидания (`acquire(blocking=False)`): в Prometheus идёт один поток, а остальные запросы к
устаревшему кэшу сразу отдают прошлую оценку. Ручка синхронная и идёт в общий threadpool, и
ждущие замок занимали бы его потоки на весь поход в Prometheus — при медленном ответе это
секунды. Прошлая оценка в это время старше TTL на время похода в Prometheus (тайм-аут адаптера —
D4). Ждут замок только запросы до первой оценки процесса: отдать им нечего. Оценка и срок её
годности лежат одной ссылкой — поток без замка читает их согласованно. Неудача кэшируется так
же — `unknown` на 15 с. Один монитор на процесс — поле контейнера; зависимость
`get_service_status_monitor` тесты подменяют.

### D6. Ручка открыта, как `/health`, но в контракте

`GET` под `/api/v1/` `OriginMiddleware` не проверяет, сессию ручка не запрашивает,
`security: []` в схеме. Ответ — модель с enum-полями, время — `UtcDatetime`. Своих ошибок нет:
в `responses` только общий `default`.

### D7. Адрес Prometheus — только API и только со стеком

`local.status_env` в `infra/locals.tf` рядом с `telemetry_env`: при `observability_enabled`
`PROMETHEUS_URL=http://dmc268-prometheus:9090`, иначе пусто. Воркеру не передаётся. В compose —
`${PROMETHEUS_URL-http://prometheus:9090}`: без профиля `observability` имя не резолвится, и
статус честно `unknown`; пустая переменная выключает запросы.

## Risks / Trade-offs

- Правила не загружены (битый `alerts.yml`) — `ALERTS` пуст, страница скажет `operational`.
  Смягчение: `promtool check config` и `promtool test rules` в CI до деплоя.
- Prometheus перезапускается — алерты с `for:` восстанавливаются из `ALERTS_FOR_STATE`, но первые
  минуты после старта новые срабатывания ещё `pending`: страница отстаёт от реальности на `for:`
  правила (2–5 минут). Это та же задержка, что у алертов команды.
- Статус отстаёт до 15 с кэша плюс интервал вычисления правил — для страницы статуса приемлемо.
- Переименование алерта или метки в `alerts.yml` без правки таблицы ловит тест домена, но только
  по именам; смысловую правку правила (другой порог) страница наследует молча — так и задумано.
