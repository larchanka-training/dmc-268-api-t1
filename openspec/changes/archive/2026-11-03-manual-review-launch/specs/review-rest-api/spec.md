## ADDED Requirements

### Requirement: Ручной запуск ревью

`POST /api/v1/reviews` SHALL создавать прогон с `trigger = manual` на текущий head-коммит PR по свежим метаданным GitHub, коммитить его и затем ставить задачу; ответ — `202` с `ReviewJob` нового прогона. Завершённый прогон коммита (любой статус и источник) SHALL NOT мешать новому. Запуск SHALL работать при `auto_review_enabled = false` и SHALL NOT применять `branch_filter`. `trigger` тела SHALL принимать только `manual`, хотя схема поля — общий `TriggerSource`.

#### Scenario: Запуск по открытому PR

- **WHEN** пользователь с ролью push на подключённом репозитории вызывает `POST /api/v1/reviews` с `{"provider": "github", "provider_repository_id": "<id>", "pull_request_number": 42, "trigger": "manual"}`, а PR #42 открыт
- **THEN** ответ — `202` с `ReviewJob`: `trigger = manual`, `status = queued`, `head_commit_sha` — текущий head PR у GitHub
- **AND** в очереди одна задача с `review_run_id` этого прогона, тем же `head_sha` и `base_sha` из метаданных PR

#### Scenario: PR ещё не встречался сервису

- **WHEN** ручной запуск приходит для PR, по которому вебхуков не было и записи запроса на изменение нет
- **THEN** запрос на изменение записывается из метаданных GitHub, прогон создаётся, ответ — `202`

#### Scenario: Повтор ревью проверенного коммита

- **WHEN** у head-коммита PR уже есть прогон `completed` (по вебхуку или вручную), и пользователь запускает ревью вручную
- **THEN** создаётся новый прогон `queued` с `trigger = manual`, задача ставится в очередь, прежний прогон остаётся доступным для чтения

#### Scenario: Ручной запуск при выключенном авторевью

- **WHEN** у подключённого репозитория `auto_review_enabled = false`, а пользователь с ролью push запускает ревью открытого PR вручную
- **THEN** ответ — `202`, создан прогон с `trigger = manual` и задача поставлена в очередь, как при включённом авторевью

#### Scenario: Фильтр веток не применяется

- **WHEN** у репозитория `branch_filter = ["release/*"]`, а PR нацелен в `main`
- **THEN** ручной запуск создаёт прогон, как без фильтра

#### Scenario: Другой источник в теле

- **WHEN** тело содержит `"trigger": "webhook"` или `"trigger": "mention"`
- **THEN** ответ — `422` `VALIDATION_ERROR` с `errors`, где `where = body.trigger`, без значения из запроса; к GitHub запросов нет, прогона нет

#### Scenario: Схема поля trigger не сужена

- **WHEN** фронтенд читает `CreateReviewRequest` в `docs/openapi/openapi.json`
- **THEN** `trigger` — `TriggerSource`, как до реализации, а описание поля говорит, что принимается только `manual`

#### Scenario: Без сессии

- **WHEN** вызывается `POST /api/v1/reviews` со своим `Origin`, но без сессии
- **THEN** ответ — `401` с `code = UNAUTHENTICATED`

### Requirement: Ручной запуск требует роль на репозитории

Ручной запуск SHALL проверять видимость и роль запросом к GitHub по токену пользователя мимо кэша видимости. Незарегистрированный, выключенный или невидимый пользователю репозиторий SHALL получать один ответ `404` `REPOSITORY_NOT_FOUND`; видимый без роли push, triage, maintain или admin — `403` `REPOSITORY_MANAGE_REQUIRED`. Отказ SHALL NOT создавать записей и ставить задачу.

#### Scenario: Роль triage

- **WHEN** пользователь с ролью triage на подключённом репозитории запускает ревью открытого PR
- **THEN** ответ — `202`

#### Scenario: Роль read на приватном репозитории

- **WHEN** пользователь с ролью read на приватном подключённом репозитории запускает ревью
- **THEN** ответ — `403` с `code = REPOSITORY_MANAGE_REQUIRED`, `detail` называет нужные роли (push, triage, maintain или admin), прогона нет, к метаданным PR запроса нет

#### Scenario: Роль отозвана после кэширования видимости

- **WHEN** репозиторий в кэше видимости пользователя с ролью push, а GitHub на прямой запрос отдаёт роль read
- **THEN** ответ — `403` с `code = REPOSITORY_MANAGE_REQUIRED`

#### Scenario: Репозиторий не виден пользователю

- **WHEN** репозиторий подключён, но GitHub на прямой запрос его пользователю не отдаёт
- **THEN** ответ — `404` с `code = REPOSITORY_NOT_FOUND`, как у незарегистрированного

#### Scenario: Репозиторий не зарегистрирован

- **WHEN** пользователь с ролью push запускает ревью репозитория, которого нет среди зарегистрированных
- **THEN** ответ — `404` с `code = REPOSITORY_NOT_FOUND`

#### Scenario: Репозиторий выключен

- **WHEN** пользователь с ролью admin запускает ревью репозитория, выключенного `DELETE /api/v1/repositories/{repository_id}`
- **THEN** ответ — `404` с `code = REPOSITORY_NOT_FOUND`, прогона нет

### Requirement: Заголовок отказа по правам общий для всех ролей

Модель `403 REPOSITORY_MANAGE_REQUIRED` SHALL нести заголовок «Недостаточно прав на репозиторий», не называющий конкретных ролей: код отдают и подключение, выключение и настройки (нужна admin или maintain), и ручной запуск (нужна push, triage, maintain или admin). Нужные роли SHALL называть `detail` конкретного отказа.

#### Scenario: Отказ подключения

- **WHEN** пользователь с ролью write вызывает `POST /api/v1/repositories`
- **THEN** ответ — `403` с `code = REPOSITORY_MANAGE_REQUIRED`, `title` «Недостаточно прав на репозиторий», а `detail` называет admin или maintain

#### Scenario: Заголовок в контракте

- **WHEN** фронтенд читает модель `REPOSITORY_MANAGE_REQUIRED` в `docs/openapi/openapi.json`
- **THEN** константа `title` — «Недостаточно прав на репозиторий»

### Requirement: Отказы ручного запуска по запросу на изменение

Ручной запуск SHALL брать номер установки приложения тем же кэшируемым способом, что дифф прогона, и свежие метаданные PR от имени установки. Нет установки — `404` `REPOSITORY_NOT_IN_INSTALLATION`; нет PR — `404` `REPOSITORY_NOT_FOUND` со своим `detail`; PR закрыт, смёржен или чужой по правилу вебхука — `422` `VALIDATION_ERROR` с `where = body.pull_request_number`; прочий сбой GitHub — `502` `SCM_UNAVAILABLE`. Отказ SHALL NOT создавать записей и ставить задачу.

#### Scenario: Приложение не установлено

- **WHEN** репозиторий подключён и виден пользователю с ролью push, а приложение с него удалено
- **THEN** ответ — `404` с `code = REPOSITORY_NOT_IN_INSTALLATION`, к метаданным PR запроса нет

#### Scenario: Нет такого PR

- **WHEN** GitHub на запрос метаданных PR отвечает `404`
- **THEN** ответ — `404` с `code = REPOSITORY_NOT_FOUND` и `detail` о том, что запроса на изменение с таким номером в репозитории нет, без номера из запроса

#### Scenario: PR закрыт

- **WHEN** PR закрыт без слияния
- **THEN** ответ — `422` `VALIDATION_ERROR` с `errors: [{"where": "body.pull_request_number", "reason": "запрос на изменение закрыт или смёржен"}]`, прогона нет

#### Scenario: PR смёржен

- **WHEN** PR смёржен
- **THEN** ответ — `422` `VALIDATION_ERROR` с тем же `where` и `reason`, прогона нет

#### Scenario: PR из форка от постороннего

- **WHEN** PR открыт из форка с `author_association = FIRST_TIME_CONTRIBUTOR`
- **THEN** ответ — `422` `VALIDATION_ERROR` с `errors: [{"where": "body.pull_request_number", "reason": "запрос на изменение из форка от автора не из участников репозитория"}]`, прогона и задачи нет
- **AND** в логе запись с репозиторием, номером PR и `author_association` полями записи

#### Scenario: PR из удалённого форка

- **WHEN** у PR пустой `head.repo`, а `author_association = CONTRIBUTOR`
- **THEN** ответ — `422` `VALIDATION_ERROR` с `where = body.pull_request_number`

#### Scenario: PR коллаборатора из форка

- **WHEN** PR открыт из форка с `author_association = COLLABORATOR`
- **THEN** ответ — `202`, прогон создан

#### Scenario: Скрытый участник организации

- **WHEN** ветка PR в самом репозитории, а `author_association = CONTRIBUTOR`
- **THEN** ответ — `202`, прогон создан

#### Scenario: GitHub не ответил на запрос метаданных

- **WHEN** GitHub отвечает `5xx` на запрос метаданных PR
- **THEN** ответ — `502` с `code = SCM_UNAVAILABLE`, прогона и задачи нет

#### Scenario: GitHub не ответил на проверке роли

- **WHEN** GitHub отвечает `5xx` на прямой запрос репозитория по токену пользователя
- **THEN** ответ — `502` с `code = SCM_UNAVAILABLE`, к метаданным PR запроса нет

### Requirement: Ручной запуск не создаёт второй активный прогон

Если у head-коммита PR уже есть прогон в нетерминальном состоянии (любого источника), ручной запуск SHALL отвечать `409` с `code = REVIEW_ALREADY_ACTIVE` и SHALL NOT менять записи и ставить задачу. Тот же ответ SHALL быть, когда вставку отклонил уникальный индекс, а после отката на этот коммит нашёлся активный прогон победителя гонки. Если победитель гонки нёс другой коммит, ручной запуск SHALL создавать прогон поверх его записи запроса на изменение, как приём вебхука.

#### Scenario: Прогон уже идёт

- **WHEN** у head-коммита PR есть прогон `analysing` по вебхуку, и пользователь запускает ревью вручную
- **THEN** ответ — `409` с `code = REVIEW_ALREADY_ACTIVE`, новых записей нет, очередь не тронута

#### Scenario: Гонка с вебхуком на тот же коммит

- **WHEN** ручной запуск и доставка вебхука одновременно создают прогон на один коммит, и уникальный индекс отклоняет вставку ручного запуска
- **THEN** ответ ручного запуска — `409` с `code = REVIEW_ALREADY_ACTIVE`, задача поставлена одна — у победителя

#### Scenario: Два ручных запуска подряд

- **WHEN** пользователь дважды запускает ревью одного PR, и первый прогон ещё `queued`
- **THEN** первый ответ — `202`, второй — `409` с `code = REVIEW_ALREADY_ACTIVE`

### Requirement: Сбой постановки ручного запуска переводит прогон в failed

Если задача ручного запуска не поставлена в очередь после коммита прогона, прогон SHALL переводиться в `failed` с причиной сбоя постановки, ответ SHALL быть `500` с `code = INTERNAL_ERROR`. Прогон `failed` SHALL NOT мешать следующему ручному запуску того же коммита.

#### Scenario: Брокер недоступен

- **WHEN** прогон ручного запуска закоммичен, а постановка задачи падает
- **THEN** ответ — `500` с `code = INTERNAL_ERROR`, прогон в `failed`, `GET /api/v1/reviews/{review_run_id}` отдаёт `trigger = manual` и `status = failed`

#### Scenario: Повтор после сбоя постановки

- **WHEN** после такого сбоя пользователь запускает ревью того же PR снова
- **THEN** ответ — `202`, создан новый прогон и задача поставлена

### Requirement: Отказы ручного запуска описаны в контракте

`POST /api/v1/reviews` в `docs/openapi/openapi.json` SHALL описывать `202` `ReviewJob` и все отказы ручного запуска существующими моделями ошибок; статус с несколькими кодами — объединением с discriminator по `code`. Ответа `501` у ручки SHALL NOT быть, а во всём контракте `501` SHALL оставаться только `PROVIDER_NOT_SUPPORTED` у `POST /api/v1/webhooks/{provider}`.

#### Scenario: Контракт ручного запуска

- **WHEN** фронтенд читает `POST /api/v1/reviews` в `docs/openapi/openapi.json`
- **THEN** у операции есть `401`, `403` (`ORIGIN_FORBIDDEN` | `REPOSITORY_MANAGE_REQUIRED`), `404` (`REPOSITORY_NOT_FOUND` | `REPOSITORY_NOT_IN_INSTALLATION`), `409` `REVIEW_ALREADY_ACTIVE`, `413`, `422` `VALIDATION_ERROR` и `502` `SCM_UNAVAILABLE`; тело запроса — `CreateReviewRequest`, ответ `202` — `ReviewJob`, и нет `501`

#### Scenario: В контракте нет заглушек

- **WHEN** перечисляются все операции `docs/openapi/openapi.json` с ответом `501`
- **THEN** находится одна — `POST /api/v1/webhooks/{provider}`, и её `501` — `PROVIDER_NOT_SUPPORTED`

## REMOVED Requirements

### Requirement: Запланированная ручка отвечает 501

**Reason**: `POST /api/v1/reviews` была последней заглушкой и теперь реализована; в контракте не осталось ручек, отвечающих `501 NOT_IMPLEMENTED`, и сценариям требования не на чем выполняться.

**Migration**: Правило для будущих заглушек остаётся в `AGENTS.md` и `.agents/rules/backend.md` («Запланированная ручка»), код `NOT_IMPLEMENTED` и его модель — в каталоге ошибок. Новая заглушка возвращает требование в спеку своим change'ем. Сценарий «Ручной запуск без сессии» переходит в требование «Ручной запуск ревью».
