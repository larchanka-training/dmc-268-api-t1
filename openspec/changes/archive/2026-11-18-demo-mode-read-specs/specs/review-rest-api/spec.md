## MODIFIED Requirements

### Requirement: Ручки чтения требуют сессию

Ручки чтения REST API — репозитории, запросы на изменение, прогоны ревью, их находки и общий поток событий `GET /api/v1/events` — SHALL требовать действительную сессию пользователя и без неё SHALL отвечать `401` с `code = UNAUTHENTICATED`. Исключение — `GET` в демо-режиме (`demo-mode`): он обслуживается как запрос демо-пользователя. Сценарии «без сессии» ручек чтения в этой спеке описывают выключенный демо-режим.

#### Scenario: Запрос без сессии

- **WHEN** демо-режим выключен и вызывается `GET /api/v1/repositories` без cookie сессии
- **THEN** ответ — `401` с `UNAUTHENTICATED`

#### Scenario: Запрос с сессией

- **WHEN** вызывается `GET /api/v1/repositories` с действительной cookie сессии
- **THEN** ответ — список репозиториев

#### Scenario: Чтение без сессии в демо-режиме

- **WHEN** демо-режим включён, демо-данные записаны и `GET /api/v1/reviews` или `GET /api/v1/repositories` вызывается без cookie сессии
- **THEN** ответ — `200` с данными демо-пользователя: только демо-прогоны и демо-репозитории

### Requirement: Дифф прогона

`GET /api/v1/reviews/{review_run_id}/diff` SHALL отдавать `200` с `ReviewDiffResponse` — дифф пары коммитов `base_sha...head_sha` прогона у GitHub: все изменённые файлы в порядке диффа, включая файлы, которые ревью отфильтровало. У файла SHALL быть статус, прежний путь у переименования, признак бинарного, язык по расширению (`null`, если не определён), число добавленных и удалённых строк и ханки по строкам; у бинарного файла ханков SHALL NOT быть. Ручка SHALL требовать сессию.

#### Scenario: Дифф прогона

- **WHEN** вызывается `GET /api/v1/reviews/{review_run_id}/diff` с сессией для прогона, видимого пользователю
- **THEN** ответ — `200` с `ReviewDiffResponse`, а не `501`

#### Scenario: Изменённый файл

- **WHEN** вызывается `GET /api/v1/reviews/{review_run_id}/diff` с сессией для видимого прогона, а в диффе у `app/main.py` заменена одна строка
- **THEN** ответ — `200`, у файла `status = modified`, `language = python`, `additions = 1`, `deletions = 1`
- **AND** в ханке строки по порядку: удалённая с `old_line` и `new_line = null`, добавленная с `new_line` и `old_line = null`, контекст с обоими номерами; `content` — без префикса `+`, `-` или пробела

#### Scenario: Добавленный и удалённый файлы

- **WHEN** дифф прогона добавляет один файл и удаляет другой
- **THEN** у первого `status = added`, `deletions = 0`, у второго `status = deleted`, `additions = 0`

#### Scenario: Переименование

- **WHEN** дифф прогона переименовывает `old.py` в `new.py`
- **THEN** у файла `path = new.py`, `previous_path = old.py`, `status = renamed`; у файлов без переименования `previous_path = null`

#### Scenario: Бинарный файл

- **WHEN** дифф прогона меняет `logo.png`
- **THEN** у файла `is_binary = true`, `hunks` пуст, `additions = deletions = 0`, `language = null`

#### Scenario: Отфильтрованный ревью файл

- **WHEN** дифф прогона меняет `uv.lock`
- **THEN** файл есть в ответе с ханками, хотя модели он не отправлялся

#### Scenario: Строка без перевода строки в конце файла

- **WHEN** в ханке есть служебная строка `\ No newline at end of file`
- **THEN** строкой ханка она не отдаётся и номера строк не сдвигает

#### Scenario: Дифф без сессии

- **WHEN** демо-режим выключен и вызывается `GET /api/v1/reviews/{review_run_id}/diff` без сессии
- **THEN** ответ — `401` с `code = UNAUTHENTICATED`

### Requirement: Шаги прогона отдаются по порядку начала

`GET /api/v1/reviews/{review_run_id}/steps` SHALL отдавать шаги воркера в прогоне (`ReviewRunStepListResponse`) по порядку начала, при равном начале — по id. Каждый шаг SHALL содержать этап, статус, начало, длительность (`null`, пока шаг идёт), токены (`null`, если шаг модель не вызывал) и код ошибки (`null`, если шаг не падал) и SHALL NOT содержать запросов и ответов модели. Повтор этапа SHALL отдаваться отдельным шагом того же этапа. Ручка SHALL требовать сессию.

#### Scenario: Шаги прогона по порядку

- **WHEN** у прогона записаны шаги, и вызывается `GET /api/v1/reviews/{review_run_id}/steps` с сессией
- **THEN** ответ — `200`, `items` — шаги этого прогона по времени начала, упавшая попытка этапа и её повтор — два шага

#### Scenario: Прогон без шагов

- **WHEN** прогон существует, но воркер ещё не начал ни одного шага
- **THEN** ответ — `200` с пустым `items`

#### Scenario: Несуществующий прогон

- **WHEN** прогона с таким id нет
- **THEN** ответ — `404` `application/problem+json` с `code = REVIEW_NOT_FOUND`

#### Scenario: Шаги без сессии

- **WHEN** демо-режим выключен и вызывается `GET /api/v1/reviews/{review_run_id}/steps` без сессии
- **THEN** ответ — `401` с `code = UNAUTHENTICATED`

#### Scenario: Брошенный шаг без токенов

- **WHEN** прогон упал, а его шаг закрыт как брошенный (`error_code = REVIEW_RUN_STALE`)
- **THEN** у шага `status = failed` и `tokens_used = null`: сколько токенов он израсходовал, неизвестно

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

- **WHEN** демо-режим выключен и вызывается `GET /api/v1/repositories` без сессии
- **THEN** ответ — `401` с `code = UNAUTHENTICATED`, к GitHub запроса нет

#### Scenario: GitHub недоступен при получении списка

- **WHEN** GitHub не ответил, ответил `429`, `5xx` или другой ошибкой, кроме `401`, или вернул тело без нужных полей, в том числе `null` в них
- **THEN** ответ — `502` с `code = SCM_UNAVAILABLE`, сессия не закрывается

#### Scenario: Подключённый, которого GitHub не отдаёт

- **WHEN** репозиторий подключён, но GitHub не отдаёт его по токену пользователя
- **THEN** строки этого репозитория в ответе нет

#### Scenario: Адрес установки приложения

- **WHEN** `GET /app` с ключом приложения отдаёт `slug = dmc-268-review`
- **THEN** в ответе `install_url = "https://github.com/apps/dmc-268-review/installations/new?state=<state>"`, где `state` привязан к сессии, а `GET /app` запрашивается один раз на процесс — тот же ответ, что у логина бота

#### Scenario: Slug не узнать

- **WHEN** у API нет ключа приложения или `GET /app` не удался
- **THEN** в ответе `install_url = null`, список отвечает `200`; после сбоя `GET /app` 60 с GitHub не спрашивается, потом спрашивается снова

#### Scenario: Ручки списка доступных нет

- **WHEN** вызывается `GET /api/v1/repositories/available`
- **THEN** в схеме такого пути нет, ответ — `422` проверки `path.repository_id`, к GitHub запроса нет

### Requirement: Настройки репозитория

`GET /api/v1/repositories/{repository_id}/settings` SHALL отдавать `200` с `RepositorySettingsResponse` — сохранёнными настройками ревью репозитория, а у репозитория, которому их не сохраняли, — `auto_review_enabled = true`, `branch_filter = []`, `min_severity = critical`. `PUT` SHALL заменять настройки телом целиком и отвечать `200` с сохранёнными. Обе SHALL требовать сессию, `PUT` — ещё `Origin` сервиса.

#### Scenario: Чтение настроек по умолчанию

- **WHEN** вызывается `GET /api/v1/repositories/{repository_id}/settings` с сессией для видимого репозитория, которому настройки не сохраняли
- **THEN** ответ — `200` с `auto_review_enabled = true`, `branch_filter = []`, `min_severity = critical`

#### Scenario: Чтение настроек

- **WHEN** вызывается `GET /api/v1/repositories/{repository_id}/settings` с сессией для видимого репозитория с сохранёнными настройками
- **THEN** ответ — `200` с сохранёнными `auto_review_enabled`, `branch_filter` и `min_severity`

#### Scenario: Запись настроек

- **WHEN** пользователь с ролью admin или maintain вызывает `PUT` настроек со своим `Origin` и телом `{"auto_review_enabled": false, "branch_filter": ["main", "release/*"], "min_severity": "high"}`
- **THEN** ответ — `200` с тем же телом, и следующий `GET` настроек отдаёт его же

#### Scenario: Полная замена

- **WHEN** после записи `branch_filter = ["main"]` вызывается `PUT` с `branch_filter = []`
- **THEN** `GET` настроек отдаёт `branch_filter = []`, прежний паттерн не остаётся

#### Scenario: Повтор того же тела

- **WHEN** `PUT` с тем же телом вызывается второй раз
- **THEN** ответ — `200` с тем же телом, сохранённые настройки не меняются

#### Scenario: Та же колонка, что в репозитории

- **WHEN** `PUT` настроек записывает `auto_review_enabled = false`
- **THEN** `GET /api/v1/repositories/{repository_id}` отдаёт `auto_review_enabled = false`

#### Scenario: Настройки после повторного включения

- **WHEN** репозиторию сохранили `auto_review_enabled = false`, `min_severity = high` и `branch_filter = ["main"]`, его выключили `DELETE /api/v1/repositories/{repository_id}` и снова подключили `POST /api/v1/repositories`
- **THEN** `GET` настроек отдаёт сохранённые `auto_review_enabled = false`, `branch_filter = ["main"]`, `min_severity = high`

#### Scenario: Без сессии

- **WHEN** демо-режим выключен и вызывается `GET` настроек без сессии
- **THEN** ответ — `401` с `code = UNAUTHENTICATED`

#### Scenario: Запись без сессии

- **WHEN** вызывается `PUT` настроек без сессии со своим `Origin` — в том числе в демо-режиме
- **THEN** ответ — `401` с `code = UNAUTHENTICATED`

#### Scenario: Запись без Origin

- **WHEN** вызывается `PUT` настроек без `Origin` или с чужим `Origin`, с сессией или без
- **THEN** ответ — `403` с `code = ORIGIN_FORBIDDEN`, тело не разбирается

#### Scenario: Значения по умолчанию в контракте

- **WHEN** фронтенд читает описание `GET` настроек в `docs/openapi/openapi.json`
- **THEN** там указано: у репозитория без сохранённых настроек `auto_review_enabled = true`, `branch_filter = []` (все ветки), `min_severity = critical`, а `501` среди ответов нет

#### Scenario: Отказ по роли описан

- **WHEN** фронтенд читает `PUT` настроек в `docs/openapi/openapi.json`
- **THEN** ответ `403` допускает `ORIGIN_FORBIDDEN` и `REPOSITORY_MANAGE_REQUIRED`, а `auto_review_enabled` описан как та же колонка, что в `RepositoryResponse`

#### Scenario: Фильтр веток описан

- **WHEN** фронтенд читает поле `branch_filter` в `docs/openapi/openapi.json`
- **THEN** в описании поля сказано, как совпадают `*` и `**`, что регистр учитывается, что PR вне фильтра получает check run `success` «Ветка не в фильтре ревью» и что ручной запуск фильтр не применяет

### Requirement: Статус правил репозитория

`GET /api/v1/repositories/{repository_id}/rules` SHALL отдавать `200` с `RulesStatusResponse` — исходом загрузки `.review/rules.md` с последнего коммита ветки по умолчанию репозитория, с теми же статусами и тем же разбором, что у загрузки правил для прогона. `branch` SHALL быть веткой по умолчанию, `path` — `.review/rules.md`. Ручка SHALL требовать сессию.

#### Scenario: Статус правил

- **WHEN** вызывается `GET /api/v1/repositories/{repository_id}/rules` с сессией для видимого репозитория
- **THEN** ответ — `200` с одним из вариантов `RulesStatusResponse`, `branch` — ветка по умолчанию, `path` — `.review/rules.md`

#### Scenario: Файл разобран

- **WHEN** на ветке по умолчанию есть `.review/rules.md` с принятыми правилами и одним отклонённым разделом
- **THEN** ответ — `200` со `status = loaded`, правилами по порядку в файле, отклонённым разделом с причиной и `fetched_at` в UTC с `Z`

#### Scenario: Ни один раздел не стал правилом

- **WHEN** файл есть, но ни один раздел не стал правилом
- **THEN** ответ — `200` со `status = empty`, отклонёнными разделами и `fetched_at`

#### Scenario: Файла нет

- **WHEN** на ветке по умолчанию нет `.review/rules.md`
- **THEN** ответ — `200` со `status = absent`, `branch` и `path`

#### Scenario: Файл больше лимита

- **WHEN** файл больше лимита размера правил
- **THEN** ответ — `200` со `status = too_large`, файл не разбирается

#### Scenario: GitHub не отдал файл

- **WHEN** GitHub не ответил на запрос установки приложения, на разрешение ветки в коммит или на чтение файла
- **THEN** ответ — `200` со `status = unavailable`, а не ошибка

#### Scenario: Без сессии или с неверным id

- **WHEN** вызывается ручка без сессии при выключенном демо-режиме или с сессией и не-UUID в пути
- **THEN** ответ — `401` с `code = UNAUTHENTICATED` или `422` с ошибкой в `path.repository_id`

#### Scenario: Варианты статуса в контракте

- **WHEN** фронтенд читает `RulesStatusResponse` в `docs/openapi/openapi.json`
- **THEN** у схемы дискриминатор `status` и пять вариантов: `loaded` (`branch`, `path`, `rules` из `RepoRule` с `name`, `patterns`, `excludes`, `checks`, `rejected` из `RejectedRule` с `name`, `reason`, `fetched_at` в UTC с `Z`), `empty` (`branch`, `path`, `rejected`, `fetched_at`), `absent`, `too_large`, `unavailable` (`branch`, `path`)

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

- **WHEN** демо-режим выключен и вызывается `GET /api/v1/repositories/{repository_id}` без сессии
- **THEN** ответ — `401` с `code = UNAUTHENTICATED`

### Requirement: Общий поток событий реализован

`GET /api/v1/events` SHALL требовать сессию и отдавать `text/event-stream`, начиная со строки `retry: 3000`. Событие SHALL быть строкой `event: <тип>` и строкой `data:` с JSON в одну строку. Событие прогона SHALL приходить не позже чем через пару секунд после записи прогона, `ping` с `data: {}` — в тишине не реже раза в 15 секунд. Истории поток SHALL не отдавать. Сессия SHALL перепроверяться каждые 5 секунд; без неё поток SHALL закрываться.

#### Scenario: Смена статуса доходит сразу

- **WHEN** поток открыт, а воркер записал прогону видимого репозитория новый статус
- **THEN** клиент получает событие не позже чем через пару секунд после коммита записи, пока поток открыт

#### Scenario: Новый прогон

- **WHEN** после подключения создан прогон видимого репозитория и это его первое событие в соединении
- **THEN** событие — `run.created` с данными `ReviewRunListItem`

#### Scenario: Изменение без итогового статуса

- **WHEN** прогон, уже показанный или созданный до подключения, изменился, а статус не итоговый — в том числе изменился только прогресс
- **THEN** событие — `run.status_changed` с последней версией строки

#### Scenario: Итоговый статус

- **WHEN** прогон оказался в статусе `completed`, `failed` или `cancelled`, даже если это его первое событие
- **THEN** событие — только `run.finished`

#### Scenario: Истории нет

- **WHEN** клиент подключается, а прогоны менялись до подключения
- **THEN** событий по этим версиям нет, клиент берёт их снимком REST после `open`

#### Scenario: Равный updated_at

- **WHEN** два прогона записаны с одним `updated_at`, второй закоммичен после того, как поток увидел первый
- **THEN** приходят события обоих, при любом порядке их id

#### Scenario: Запоздавшая запись

- **WHEN** запись прогона закоммичена позже соседней, а её `updated_at` меньше уже увиденного, но не больше чем на 5 секунд
- **THEN** событие по ней приходит

#### Scenario: Тишина

- **WHEN** 15 секунд нет событий
- **THEN** приходит `event: ping` с `data: {}`

#### Scenario: Сессия истекла

- **WHEN** сессия закончилась, пока поток открыт
- **THEN** поток закрывается без события не позже чем через 5 секунд

#### Scenario: Без сессии

- **WHEN** демо-режим выключен и вызывается `GET /api/v1/events` без сессии
- **THEN** ответ — `401` с `code = UNAUTHENTICATED`, поток не открывается

#### Scenario: Поток в демо-режиме

- **WHEN** демо-режим включён и `GET /api/v1/events` вызывается без сессии
- **THEN** поток открывается для демо-пользователя, и перепроверка сессии его не закрывает

#### Scenario: Контракт ответов

- **WHEN** фронтенд читает `GET /api/v1/events` в `docs/openapi/openapi.json`
- **THEN** у операции нет параметров, есть ответы `200` `text/event-stream`, `401` и `502`, ответа `501` нет; пути `/api/v1/reviews/events` нет, а `GET /api/v1/reviews/events` отвечает `422` проверки id прогона

#### Scenario: Трасса потока

- **WHEN** поток открыт часами, а клиент затем закрывает соединение
- **THEN** чтения базы потоком не пишут спанов SQL, а спан запроса закрывается без статуса ERROR

### Requirement: Список прогонов ревью

`GET /api/v1/reviews` SHALL требовать сессию и SHALL отвечать `200` со страницей прогонов: `items` — строки `ReviewRunListItem` с `MergeRequestSummary`, числом находок и обязательным `last_progress_at`, и `total` — число прогонов под фильтрами без учёта `limit` и `offset`. Порядок SHALL быть по `created_at` по убыванию, при равном — по `id` по убыванию. Строки страницы SHALL читаться числом запросов к базе, не зависящим от размера страницы.

#### Scenario: Страница не меняет total

- **WHEN** вызывается `GET /api/v1/reviews` с `limit=2&offset=1`, а под фильтры подходят четыре прогона
- **THEN** в `items` второй и третий прогоны по порядку списка, `total = 4`

#### Scenario: Список без сессии

- **WHEN** демо-режим выключен и вызывается `GET /api/v1/reviews` без сессии
- **THEN** ответ — `401` с `code = UNAUTHENTICATED`

#### Scenario: Контракт списка для фронтенда

- **WHEN** фронтенд читает `docs/openapi/openapi.json`
- **THEN** в `ReviewRunListResponse` есть обязательные `items` и `total`, в `ReviewRunListItem` — обязательный `last_progress_at` в UTC

### Requirement: Параметры списка прогонов проверяются раньше обращения к GitHub

`GET /api/v1/reviews` SHALL проверять параметры раньше, чем запросит у GitHub набор видимых репозиториев: `422` SHALL не стоить запроса к провайдеру. `status` и `repository_id` SHALL принимать несколько значений, `repository_id` — не больше 50. Пустые `q` и `author` SHALL означать «без фильтра». `created_from` и `created_to` SHALL быть временем ISO 8601 с зоной; `created_from` позже `created_to` — `422`. `limit` выше 200 SHALL приводиться к 200.

#### Scenario: Неверный параметр не доходит до GitHub

- **WHEN** вызывается `GET /api/v1/reviews` с сессией и значением `status` вне `ReviewRunStatus`
- **THEN** ответ — `422` с `code = VALIDATION_ERROR`, а к GitHub запроса не было

#### Scenario: Обратный диапазон дат

- **WHEN** вызывается `GET /api/v1/reviews` с сессией и `created_from` позже `created_to`
- **THEN** ответ — `422` с `code = VALIDATION_ERROR` и ошибкой в `query.created_to`

#### Scenario: Время без зоны или не ISO 8601

- **WHEN** вызывается `GET /api/v1/reviews` с сессией и `created_from` без зоны или `created_to` в виде unix-времени
- **THEN** ответ — `422` с `code = VALIDATION_ERROR` и причиной «ожидается время с зоной» или «ожидается время в ISO 8601»

#### Scenario: Без сессии параметры не проверяются

- **WHEN** демо-режим выключен и вызывается `GET /api/v1/reviews` без сессии и с неверными параметрами
- **THEN** ответ — `401` с `code = UNAUTHENTICATED`

#### Scenario: Неверный id репозитория среди нескольких

- **WHEN** вызывается `GET /api/v1/reviews` с сессией и `repository_id` дважды, второй раз не UUID
- **THEN** ответ — `422` с `code = VALIDATION_ERROR` и ошибкой в `query.repository_id.1`

#### Scenario: Больше 50 репозиториев

- **WHEN** вызывается `GET /api/v1/reviews` с сессией и 51 значением `repository_id`
- **THEN** ответ — `422` с причиной «элементов должно быть не больше 50» в `query.repository_id`

#### Scenario: Время за краем календаря после сдвига зоны

- **WHEN** вызывается `GET /api/v1/reviews` с сессией и `created_from=0001-01-01T00:00:00+01:00`
- **THEN** ответ — `422` с причиной «время вне допустимого диапазона» в `query.created_from`, а не `500`

#### Scenario: NUL в тексте фильтра

- **WHEN** вызывается `GET /api/v1/reviews` с сессией и символом NUL в `q` или `author`
- **THEN** ответ — `422` с причиной «строка не должна содержать символ NUL», а не `500`

### Requirement: Аналитика кабинета

`GET /api/v1/analytics` SHALL требовать сессию и SHALL отвечать `200` с агрегатами за период: `findings_by_day` — замечания по UTC-дню прогона, категории и важности, по дню, затем категории и важности в порядке перечислений; `runs` — число, средняя и p95 длительность прогонов; `by_repository` — то же и число замечаний по каждому репозиторию с учтёнными прогонами, по `full_name`. Учитываются прогоны `completed`, кроме `coverage_reason = balance`, и только новые замечания, без повторов тредов.

#### Scenario: Замечания по дням, категориям и важности

- **WHEN** в периоде прогоны видимого репозитория нашли два замечания `security`/`high` и одно `security`/`low` 3 октября по UTC
- **THEN** в `findings_by_day` есть `{date: 2026-10-03, category: security, severity: high, count: 2}` и `{date: 2026-10-03, category: security, severity: low, count: 1}`, пар без замечаний нет

#### Scenario: Длительность всего и по репозиторию

- **WHEN** в периоде завершены прогоны двух видимых репозиториев с длительностью
- **THEN** `runs` несёт их число, среднюю и p95 (`percentile_cont(0.95)`), а `by_repository` — те же величины, `repository_id`, `full_name` и `findings_count` по каждому репозиторию

#### Scenario: Прогон без денег и незавершённый прогон

- **WHEN** в периоде есть прогон с `coverage_reason = balance`, упавший и идущий прогоны
- **THEN** ни они, ни их замечания в агрегаты не входят

#### Scenario: Повтор треда бота

- **WHEN** более поздний прогон того же запроса на изменение повторил замечание открытого треда бота (`covered_by_finding_id` задан)
- **THEN** повтор не входит ни в `findings_by_day`, ни в `findings_count`, а новое замечание того же прогона входит

#### Scenario: Данных нет

- **WHEN** в периоде нет учтённых прогонов
- **THEN** ответ — `200` с пустыми `findings_by_day` и `by_repository` и `runs = {count: 0, average_duration_seconds: null, p95_duration_seconds: null}`

#### Scenario: Без сессии

- **WHEN** демо-режим выключен и вызывается `GET /api/v1/analytics` без сессии
- **THEN** ответ — `401` с `code = UNAUTHENTICATED`

#### Scenario: Контракт для фронтенда

- **WHEN** фронтенд читает `docs/openapi/openapi.json`
- **THEN** у `GET /api/v1/analytics` описаны `AnalyticsResponse` с обязательными `created_from`, `created_to`, `findings_by_day`, `runs`, `by_repository` и ответы `401`, `404`, `422`, `502`
