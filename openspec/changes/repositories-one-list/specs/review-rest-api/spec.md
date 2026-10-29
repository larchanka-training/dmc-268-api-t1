## RENAMED Requirements

- FROM: `### Requirement: Список репозиториев, доступных для подключения`
- TO: `### Requirement: Список репозиториев установок, видимых пользователю`

- FROM: `### Requirement: Признак права на подключение в списке доступных`
- TO: `### Requirement: Признак права на подключение в списке репозиториев`

## MODIFIED Requirements

### Requirement: Список репозиториев постранично

Система SHALL предоставлять `GET /api/v1/repositories`, возвращающий
постраничный список репозиториев установок, видимых пользователю, —
подключённых и нет (требование «Список репозиториев установок, видимых
пользователю»), по `full_name` без учёта регистра.
Запрос SHALL
принимать `limit` и `offset`; при отсутствии `limit` SHALL применяться
значение по умолчанию, а значение выше зафиксированного потолка SHALL быть
приведено к этому потолку, а не отклонено ошибкой.

#### Scenario: Список репозиториев возвращается постранично

- **WHEN** вызывается `GET /api/v1/repositories` без параметров
- **THEN** ответ содержит репозитории числом не больше значения по
  умолчанию

#### Scenario: `limit` выше потолка не отклоняется

- **WHEN** вызывается `GET /api/v1/repositories` с `limit` больше
  зафиксированного потолка
- **THEN** ответ успешен и содержит не больше репозиториев, чем позволяет
  потолок

#### Scenario: Страница по имени

- **WHEN** GitHub отдаёт пользователю `Octo/Alpha`, `octo/bravo` и `octo/charlie`, подключён только `octo/charlie`, и вызывается `GET /api/v1/repositories` с `limit=2` и `offset=1`
- **THEN** ответ — `octo/bravo` и `octo/charlie` в этом порядке

### Requirement: Список репозиториев установок, видимых пользователю

`GET /api/v1/repositories` SHALL требовать сессию и SHALL отдавать репозитории установок, которые GitHub отдаёт по токену пользователя, подключённые и нет: `id` записи или `null`, `connected` и `connected_at` по включённости ревью, у ответа — `install_url`. Прочее SHALL NOT попадать в ответ. Приостановленная установка и установка с `403` (не лимит) или `404` SHALL пропускаться. Страниц SHALL быть не больше 50. Сбой GitHub, `403` лимита и неожиданное тело SHALL давать `502 SCM_UNAVAILABLE`.

#### Scenario: Список с отметкой подключённых

- **WHEN** по токену пользователя GitHub отдаёт репозитории `octo-org/api` и `octo-org/web`, `octo-org/api` подключён, и вызывается `GET /api/v1/repositories` с сессией
- **THEN** ответ — `200` с обоими репозиториями, у `octo-org/api` `connected = true`, `id` и `connected_at` записи, у `octo-org/web` — `connected = false`, `id = null` и `connected_at = null`
- **AND** `full_name` и `default_branch` строк — из ответа GitHub

#### Scenario: Репозиторий только с правом чтения показывается

- **WHEN** GitHub отдаёт `octo-org/web` с `permissions.admin = false` и `permissions.maintain = false`
- **THEN** `octo-org/web` есть в ответе

#### Scenario: Репозиторий без доступа не показывается

- **WHEN** `octo-org/api` есть в установке приложения, но у вошедшего пользователя нет к нему доступа, и GitHub не отдаёт его по токену пользователя
- **THEN** `octo-org/api` в ответе нет, ответ — `200`

#### Scenario: Чужой приватный репозиторий не показывается

- **WHEN** `octo-org/secret` приватный, в установке приложения, а вошедший пользователь к нему доступа не имеет
- **THEN** `octo-org/secret` в ответе нет: сервис показывает только то, что GitHub отдал по токену пользователя, и сам доступ не вычисляет

#### Scenario: Публичный репозиторий без явного права не показывается

- **WHEN** `octo-org/public` публичный, в установке приложения, а у вошедшего пользователя на нём нет роли (читать его может любой), и GitHub не отдаёт его по `/user/installations…`
- **THEN** `octo-org/public` в ответе нет, ответ — `200`: публичность репозитория видимости не даёт

#### Scenario: Приостановленная установка

- **WHEN** одна из установок пользователя приостановлена (`suspended_at` не `null`)
- **THEN** её репозитории не запрашиваются, ответ — `200` с репозиториями остальных установок

#### Scenario: Установка закрыта для токена

- **WHEN** GitHub отвечает `403` без признаков лимита на репозитории одной установки (например, организация требует SAML SSO)
- **THEN** установка пропускается, ответ — `200` с репозиториями остальных установок

#### Scenario: Лимит GitHub

- **WHEN** GitHub отвечает `403` с `x-ratelimit-remaining: 0`, с `retry-after` или с «rate limit» в `message` тела
- **THEN** ответ — `502` с `code = SCM_UNAVAILABLE`

#### Scenario: Ответ, который не кончается

- **WHEN** GitHub отдаёт полные страницы дольше 50 страниц
- **THEN** запрашивается не больше 50 страниц, список усечён

#### Scenario: Доступа нет ни к одному репозиторию

- **WHEN** GitHub не отдаёт пользователю ни одного репозитория установок
- **THEN** ответ — `200` с пустым `items`

#### Scenario: Список без сессии

- **WHEN** вызывается `GET /api/v1/repositories` без сессии
- **THEN** ответ — `401` с `code = UNAUTHENTICATED`, к GitHub запроса нет

#### Scenario: GitHub недоступен при получении списка

- **WHEN** GitHub не ответил, ответил `429`, `5xx` или другой ошибкой, кроме `401`, или вернул тело без нужных полей, в том числе `null` в них
- **THEN** ответ — `502` с `code = SCM_UNAVAILABLE`, сессия не закрывается

#### Scenario: Подключённый, которого GitHub не отдаёт

- **WHEN** репозиторий подключён, но GitHub не отдаёт его по токену пользователя
- **THEN** строки этого репозитория в ответе нет

#### Scenario: Адрес установки приложения

- **WHEN** `GET /app` с ключом приложения отдаёт `slug = dmc-268-review`
- **THEN** в ответе `install_url = "https://github.com/apps/dmc-268-review/installations/new"`, а `GET /app` запрашивается один раз на процесс — тот же ответ, что у логина бота

#### Scenario: Slug не узнать

- **WHEN** у API нет ключа приложения или `GET /app` не удался
- **THEN** в ответе `install_url = null`, список отвечает `200`; сбой `GET /app` не запоминается

#### Scenario: Ручки списка доступных нет

- **WHEN** вызывается `GET /api/v1/repositories/available`
- **THEN** в схеме такого пути нет, ответ — `422` проверки `path.repository_id`, к GitHub запроса нет

### Requirement: Подключение репозитория

`POST /api/v1/repositories` SHALL требовать сессию и `Origin` сервиса и SHALL регистрировать репозиторий, найденный двумя запросами к GitHub (по id токеном пользователя, затем установка приложения), без обхода списка: `auto_review_enabled = true`, `full_name` и `default_branch` — из ответа GitHub, ответ — `201`. Выключенный SHALL включаться обратно с прежним `id`. `installation_id` SHALL NOT храниться. Сбой GitHub SHALL давать `502 SCM_UNAVAILABLE` без записи.

#### Scenario: Подключение администратором

- **WHEN** вызывается `POST /api/v1/repositories` с сессией пользователя, у которого на репозиторий `permissions.admin = true`, своим `Origin` и телом `{"provider": "github", "provider_id": "123456789"}`
- **THEN** ответ — `201` с репозиторием, `auto_review_enabled = true`
- **AND** репозиторий записан в `repositories`

#### Scenario: Два запроса к GitHub

- **WHEN** подключается репозиторий, видимый пользователю, с установленным приложением
- **THEN** к GitHub уходят ровно два запроса: репозиторий по id токеном пользователя и установка приложения на нём

#### Scenario: Повторное включение выключенного

- **WHEN** репозиторий выключен `DELETE /api/v1/repositories/{repository_id}`, и пользователь с admin или maintain вызывает `POST /api/v1/repositories` с его `provider_id`
- **THEN** ответ — `201` с прежним `id`, в `GET /api/v1/repositories` у него `connected = true`, прогоны и запросы на изменение на месте

### Requirement: Отказы подключения репозитория

Подключать репозиторий SHALL только пользователь с `permissions.admin` или `permissions.maintain`. Порядок: не `github`, не виден пользователю или публичный только с `pull`, приложение не установлено или приостановлено — `404 REPOSITORY_NOT_IN_INSTALLATION` (установка — после доступа); нет admin и maintain — `403 REPOSITORY_MANAGE_REQUIRED`; уже включён — `409 REPOSITORY_ALREADY_CONNECTED`. Все `404` SHALL быть одинаковы; без прав SHALL NOT быть видно, подключён ли репозиторий.

#### Scenario: Подключение с ролью maintain

- **WHEN** у вошедшего пользователя на репозиторий `permissions.maintain = true` и `permissions.admin = false`
- **THEN** ответ — `201`, репозиторий записан

#### Scenario: Подключение без прав admin

- **WHEN** GitHub отдаёт репозиторий пользователю с `permissions.push = true` или `permissions.triage = true`, но с `permissions.admin = false` и `permissions.maintain = false`
- **THEN** ответ — `403` с `code = REPOSITORY_MANAGE_REQUIRED`, записи нет

#### Scenario: Репозиторий только с правом чтения

- **WHEN** GitHub отдаёт публичный (`private = false`) репозиторий по id с `permissions.push`, `triage`, `maintain` и `admin` равными `false` — так он отдаёт любому чужой публичный репозиторий
- **THEN** ответ — `404` с `code = REPOSITORY_NOT_IN_INSTALLATION`, тело совпадает с ответом на отсутствующий репозиторий, кроме `trace_id`, установка приложения не запрашивается, и ответ не зависит от того, установлено ли туда приложение

#### Scenario: Приватный репозиторий только с правом чтения

- **WHEN** GitHub отдаёт приватный (`private = true`) репозиторий по id с `permissions.push`, `triage`, `maintain` и `admin` равными `false` — у пользователя роль read, а приложение на репозитории установлено
- **THEN** ответ — `403` с `code = REPOSITORY_MANAGE_REQUIRED`, записи нет: приватный репозиторий GitHub отдаёт только участнику, и отказ ему ничего не раскрывает

#### Scenario: Приватный репозиторий только с правом чтения без приложения

- **WHEN** у пользователя роль read на приватном репозитории, а приложение на нём не установлено или установка приостановлена
- **THEN** ответ — `404` с `code = REPOSITORY_NOT_IN_INSTALLATION`

#### Scenario: Подключение уже подключённого без прав

- **WHEN** репозиторий уже зарегистрирован, а у вошедшего пользователя нет ни admin, ни maintain на него
- **THEN** ответ — `403` с `code = REPOSITORY_MANAGE_REQUIRED`, а не `409`

#### Scenario: Повторное подключение

- **WHEN** репозиторий уже подключён и не выключен, и подключает пользователь с admin или maintain, в том числе параллельно с другим
- **THEN** ответ — `409` с `code = REPOSITORY_ALREADY_CONNECTED`, запись одна; интерфейс считает этот ответ успехом

#### Scenario: Репозиторий не из установки

- **WHEN** `provider_id` нет среди репозиториев установок приложения
- **THEN** ответ — `404` с `code = REPOSITORY_NOT_IN_INSTALLATION`

#### Scenario: Невидимый репозиторий неотличим от отсутствующего

- **WHEN** репозиторий есть в установке, но GitHub не отдаёт его по токену пользователя
- **THEN** ответ — `404` с `code = REPOSITORY_NOT_IN_INSTALLATION`, тело совпадает с ответом на отсутствующий репозиторий, кроме `trace_id`

#### Scenario: Приложение не установлено или установка приостановлена

- **WHEN** пользователь видит репозиторий, но приложение на нём не установлено или установка приостановлена
- **THEN** ответ — `404` с `code = REPOSITORY_NOT_IN_INSTALLATION`, тело совпадает с ответом на невидимый репозиторий, кроме `trace_id`

#### Scenario: Установка не проверяется для чужого репозитория

- **WHEN** пользователь не видит репозиторий
- **THEN** установка приложения на нём не запрашивается, ответ — `404`

#### Scenario: GitHub недоступен при подключении

- **WHEN** GitHub не ответил или ответил ошибкой, кроме `401`
- **THEN** ответ — `502` с `code = SCM_UNAVAILABLE`, записи нет

#### Scenario: Подключение без Origin

- **WHEN** вызывается `POST /api/v1/repositories` без `Origin`
- **THEN** ответ — `403` с `code = ORIGIN_FORBIDDEN`

#### Scenario: Подключение с пустым идентификатором

- **WHEN** вызывается `POST /api/v1/repositories` с сессией, своим `Origin` и пустым `provider_id`
- **THEN** ответ — `422` с `{"where": "body.provider_id", "reason": "длина строки должна быть не меньше 1"}`

### Requirement: Один репозиторий по id

`GET /api/v1/repositories/{repository_id}` SHALL требовать сессию и SHALL отдавать зарегистрированный репозиторий в форме `RepositoryResponse`, в том числе выключенный: история остаётся. Его `id` — тот же, что у строки `GET /api/v1/repositories`. Репозитория нет — `404` с `code = REPOSITORY_NOT_FOUND`; id не UUID — `422`.

#### Scenario: Репозиторий по id

- **WHEN** репозиторий зарегистрирован, и вызывается `GET /api/v1/repositories/{repository_id}` с сессией
- **THEN** ответ — `200`, `id` и `full_name` совпадают со строкой этого репозитория в `GET /api/v1/repositories`

#### Scenario: Репозиторий за пределами первой страницы списка

- **WHEN** зарегистрировано больше репозиториев, чем помещается на страницу списка, и запрашивается последний по id
- **THEN** ответ — `200` с этим репозиторием

#### Scenario: Нет такого репозитория

- **WHEN** репозитория с таким id нет
- **THEN** ответ — `404` `application/problem+json` с `code = REPOSITORY_NOT_FOUND`

#### Scenario: id не UUID

- **WHEN** вызывается `GET /api/v1/repositories/repo-1` с сессией
- **THEN** ответ — `422` с ошибкой в `path.repository_id`

#### Scenario: Без сессии

- **WHEN** вызывается `GET /api/v1/repositories/{repository_id}` без сессии
- **THEN** ответ — `401` с `code = UNAUTHENTICATED`

### Requirement: Типы и видимость событий общего потока описаны

Имя события `GET /api/v1/events` SHALL быть `<ресурс>.<действие>` в поле `event:`, данные — модель своего типа, перечисленная в `x-sse-events`. Клиент SHALL игнорировать незнакомые типы: новые добавляются без правки клиентов. Пользователь SHALL получать только видимые ему события; видимость задаёт правило типа события. Фильтров у потока нет.

#### Scenario: Сегодняшние типы

- **WHEN** фронтенд читает ответ потока и `components/schemas` в `docs/openapi/openapi.json`
- **THEN** в `x-sse-events` есть `run.created`, `run.status_changed` и `run.finished` со ссылкой на `ReviewRunListItem` и `ping` с пустым объектом, а `StreamEventType` перечисляет эти четыре имени

#### Scenario: Итоговый статус

- **WHEN** фронтенд читает описание потока
- **THEN** в нём сказано, что при переходе прогона в итоговый статус приходит только `run.finished`

#### Scenario: Видимость прогонов

- **WHEN** пользователь подключается к потоку
- **THEN** набор видимых репозиториев считается один раз тем же расчётом, что строки `GET /repositories`, и до переподключения не пересчитывается; события `run.*` по прогонам остальных репозиториев не приходят

#### Scenario: Сбой провайдера при подключении

- **WHEN** GitHub не ответил или ответил ошибкой, пока считается набор видимых репозиториев
- **THEN** ответ — `502` с `code = SCM_UNAVAILABLE`, поток не открывается

#### Scenario: Незнакомый тип

- **WHEN** фронтенд читает описание потока
- **THEN** в нём сказано, что клиент игнорирует незнакомые типы событий

#### Scenario: Прогоны публичного репозитория без явного права

- **WHEN** у пользователя нет роли на публичном репозитории `octo-org/public`, к которому подключено приложение, и по `octo-org/public` идёт прогон
- **THEN** события `run.*` этого прогона пользователю не приходят: `octo-org/public` нет в ответе `GET /repositories`

### Requirement: Истёкший токен пользователя обновляется

Ручки, обращающиеся к GitHub по токену пользователя (`GET /repositories`, `POST /repositories`, подключение к `GET /events`), SHALL обменивать истёкший access token по refresh token и сохранять новую пару. Сбой обмена или ожидание чужого дольше 15 секунд SHALL давать `502 SCM_UNAVAILABLE` без закрытия сессии, если до истечения access token меньше минуты; иначе запрос идёт прежним токеном. Два параллельных запроса одной сессии SHALL обменивать refresh token не больше одного раза.

#### Scenario: Истёкший токен обновляется

- **WHEN** access token сессии истёк, refresh token действует, и вызывается `GET /api/v1/repositories`
- **THEN** токен обменивается по refresh token, новая пара сохраняется, ответ — `200`

#### Scenario: Токен без истечения

- **WHEN** GitHub выдал при входе токен без `expires_in`
- **THEN** токен используется без обмена, сколько бы времени ни прошло

#### Scenario: GitHub недоступен при обмене

- **WHEN** access token истёк, а обмен refresh token не удался из-за сети, `5xx` или ошибки настройки приложения
- **THEN** ответ — `502` с `code = SCM_UNAVAILABLE`, сессия и токен остаются

#### Scenario: Сбой обмена при ещё действующем токене

- **WHEN** до истечения access token меньше 5 минут, но больше минуты, и обмен не удался из-за сбоя GitHub или чужая блокировка не отпущена за 15 секунд
- **THEN** запрос выполняется прежним access token, сессия и токен остаются

#### Scenario: Два запроса одной сессии с истёкшим токеном

- **WHEN** два запроса одной сессии одновременно видят истёкший access token
- **THEN** refresh token обменивается один раз, оба запроса используют новый токен

#### Scenario: Обмен при подключении к общему потоку

- **WHEN** access token сессии истёк, refresh token действует, и клиент подключается к `GET /api/v1/events`
- **THEN** токен обменивается до начала потока, видимые репозитории считаются новым токеном, поток открывается; если GitHub при обмене недоступен — `502` с `code = SCM_UNAVAILABLE`, поток не открывается, сессия остаётся

### Requirement: Список прогонов показывает только видимые репозитории

`GET /api/v1/reviews` SHALL отдавать только прогоны репозиториев, к которым у пользователя есть доступ на GitHub, — то же правило, что у `GET /repositories` и потока `GET /events`. Набор видимых SHALL считаться токеном пользователя; в пределах TTL кэша SHALL браться из кэша (требование «Набор видимых репозиториев кэшируется»); сбой GitHub SHALL давать `502` с `code = SCM_UNAVAILABLE`.

#### Scenario: Прогон невидимого репозитория

- **WHEN** вызывается `GET /api/v1/reviews` с сессией, а среди подключённых репозиториев есть такой, которого GitHub не отдаёт по токену пользователя
- **THEN** прогонов этого репозитория нет ни в `items`, ни в `total`

#### Scenario: Невидимый репозиторий в фильтре

- **WHEN** вызывается `GET /api/v1/reviews` с `repository_id` невидимого или несуществующего репозитория
- **THEN** ответ — `200` с пустым `items` и `total = 0`, а не `404`

#### Scenario: GitHub недоступен

- **WHEN** вызывается `GET /api/v1/reviews` с сессией, а GitHub не ответил на запрос списка репозиториев
- **THEN** ответ — `502` с `code = SCM_UNAVAILABLE`

### Requirement: Набор видимых репозиториев кэшируется

Ответ GitHub о репозиториях, видимых пользователю, SHALL кэшироваться на `VISIBLE_REPOSITORIES_CACHE_TTL_SECONDS` (по умолчанию 30 с) для `GET /reviews`, `GET /events`, ручек прогона и репозиториев, включая список. Подключён ли репозиторий, SHALL читаться из базы на каждый запрос. Возврат с установки (Setup URL) SHALL спрашивать GitHub и записывать свежий ответ в кэш; проверки прав подключения и выключения SHALL идти мимо кэша.

#### Scenario: Повтор в пределах TTL

- **WHEN** пользователь дважды за TTL запрашивает `GET /api/v1/reviews` или `GET /api/v1/repositories`
- **THEN** к GitHub за списком репозиториев был один запрос

#### Scenario: Подключённый репозиторий виден сразу

- **WHEN** пользователь подключает репозиторий и сразу запрашивает `GET /api/v1/repositories`
- **THEN** репозиторий в списке, без ожидания TTL

#### Scenario: Страница подключения без кэша

- **WHEN** пользователь возвращается с экрана установки на `GET /api/v1/auth/github/setup` в пределах TTL после другого запроса видимости
- **THEN** список репозиториев запрошен у GitHub заново, и свежий ответ записан в кэш

#### Scenario: Приложение поставлено в пределах TTL

- **WHEN** кэш видимости заполнен, пользователь ставит приложение на новый репозиторий на GitHub и возвращается на Setup URL
- **THEN** репозиторий сразу есть в `GET /api/v1/repositories`, а подключённый — отвечает `200` на `GET /api/v1/repositories/{repository_id}`

### Requirement: Признак права на подключение в списке репозиториев

Каждая строка `GET /api/v1/repositories` SHALL нести `can_connect`: `true`, если у пользователя на репозитории роль admin или maintain, иначе `false`. Правило SHALL совпадать с проверкой прав `POST` и `DELETE /api/v1/repositories`, права SHALL браться из того же ответа GitHub, что и список. Ручки SHALL проверять права сами, независимо от поля.

#### Scenario: Роль admin или maintain

- **WHEN** GitHub отдаёт `octo-org/api` с `permissions.admin = true` или `permissions.maintain = true`
- **THEN** у `octo-org/api` в ответе `can_connect = true`, и `POST /repositories` с его `provider_id` его подключает (`201`) или отвечает `409`, если он уже подключён

#### Scenario: Роль ниже maintain

- **WHEN** GitHub отдаёт `octo-org/web` с ролью read, triage или write
- **THEN** у `octo-org/web` в ответе `can_connect = false`, и `POST /repositories` с его `provider_id` его не подключает: `403` с `code = REPOSITORY_MANAGE_REQUIRED`, а публичный только с правом чтения — `404`, как невидимый (такую строку GitHub в список не отдаёт: это ответ на прямой `POST`)

## ADDED Requirements

### Requirement: Выключение ревью репозитория

`DELETE /api/v1/repositories/{repository_id}` SHALL требовать сессию и `Origin` сервиса и SHALL выключать ревью мягко: запись, прогоны и запросы на изменение остаются, ответ — `204`. Невидимый или несуществующий — `404 REPOSITORY_NOT_FOUND`; нет admin и maintain (запрос к GitHub мимо кэша) — `403 REPOSITORY_MANAGE_REQUIRED`. Уже выключен — `204`. Сбой GitHub — `502 SCM_UNAVAILABLE`, ревью не выключено.

#### Scenario: Выключение администратором

- **WHEN** у пользователя на подключённом репозитории роль admin или maintain, и вызывается `DELETE /api/v1/repositories/{repository_id}` со своим `Origin`
- **THEN** ответ — `204`, в `GET /api/v1/repositories` у репозитория `connected = false`, `connected_at = null` и прежний `id`, а `GET /api/v1/repositories/{repository_id}` и его прогоны отвечают как раньше

#### Scenario: Уже выключен

- **WHEN** репозиторий уже выключен, в том числе параллельным запросом, и вызывается `DELETE` ещё раз
- **THEN** ответ — `204`

#### Scenario: Без прав

- **WHEN** у пользователя на репозитории роль write, triage или read
- **THEN** ответ — `403` с `code = REPOSITORY_MANAGE_REQUIRED`, ревью не выключено

#### Scenario: Невидимый репозиторий

- **WHEN** репозитория с таким id нет или GitHub не отдаёт его пользователю
- **THEN** ответ — `404` с `code = REPOSITORY_NOT_FOUND`, тело одно и то же, кроме `trace_id`

#### Scenario: Без Origin

- **WHEN** вызывается `DELETE /api/v1/repositories/{repository_id}` без `Origin`
- **THEN** ответ — `403` с `code = ORIGIN_FORBIDDEN`, раньше проверки сессии

#### Scenario: Включение и выключение одновременно

- **WHEN** `POST` и `DELETE` одного репозитория приходят одновременно
- **THEN** запись одна, и побеждает операция, завершившаяся последней

### Requirement: Возврат с установки GitHub App

`GET /api/v1/auth/github/setup` SHALL требовать сессию (без неё — `302` на `/login?redirect=/app/repositories`) и SHALL сверять `installation_id` с установками пользователя по его токену. При «Only select repositories» SHALL подключаться репозитории установки, где у пользователя admin или maintain и которых нет в сервисе; при «All repositories» — ничего; выключенные SHALL NOT включаться. Ответ SHALL быть `302` на `/app/repositories` с итогом.

#### Scenario: Выбранные репозитории

- **WHEN** пользователь установил приложение на два выбранных репозитория, на одном у него admin, на другом write, и GitHub вернул его на Setup URL с `setup_action=install`
- **THEN** ответ — `302` на `/app/repositories?connected=1`, подключён репозиторий с admin

#### Scenario: Все репозитории

- **WHEN** установка с `repository_selection = all`
- **THEN** ответ — `302` на `/app/repositories?connected=0`, ничего не подключено

#### Scenario: Запрос владельцу

- **WHEN** `setup_action=request` — пользователь без прав запросил установку у владельца организации
- **THEN** ответ — `302` на `/app/repositories?requested=true`, ничего не подключено

#### Scenario: Чужая установка

- **WHEN** `installation_id` нет среди установок, которые GitHub отдаёт по токену пользователя, или параметра нет при `install` или `update`
- **THEN** ответ — `302` на `/app/repositories?setup_error=installation_not_found`, ничего не подключено

#### Scenario: GitHub недоступен

- **WHEN** GitHub не ответил при сверке установки или списка репозиториев
- **THEN** ответ — `302` на `/app/repositories?setup_error=provider_unavailable`

#### Scenario: Без сессии

- **WHEN** вызывается `GET /api/v1/auth/github/setup` без сессии или с недействительной cookie `session`
- **THEN** ответ — `302` на `/login?redirect=/app/repositories`, недействительная cookie удаляется, ничего не подключено

#### Scenario: Неверные параметры

- **WHEN** `setup_action` нет или он не `install`, `update` или `request`, либо `installation_id` не целое больше нуля
- **THEN** ответ — `422`, к GitHub запроса нет
