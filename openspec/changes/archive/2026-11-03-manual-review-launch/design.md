## Context

Мотивация — `proposal.md`, поведение — дельта в `specs/`. Что есть сейчас:

- `create_review` (`app/api/reviews.py`) — заглушка: `CreateReviewRequest` (`provider`,
  `provider_repository_id`, `pull_request_number`, `trigger: TriggerSource`), ответ `202 ReviewJob`
  в схеме, в `responses` — `403 ORIGIN`, `409 REVIEW_ALREADY_ACTIVE`, `413`, `422`, `501`. Тело
  `ReviewJob` (`app/api/schemas.py`) собирает `_review_job(outcome)` в `app/api/webhooks.py`.
- `handle_webhook_event` (`app/application/use_cases/handle_webhook.py`) — две половины. Первая —
  разбор события, `is_trusted_author(event)`, поиск репозитория, `pause_check`, метаданные PR.
  Вторая — дубль (`find_active` или `find_reviewed`), upsert merge_request, прогон с
  `TriggerSource.WEBHOOK`, коммит, разбор гонки `ActiveRunConflict` (`_find_survivor`, повтор поверх
  записей победителя с другим коммитом), постановка `ReviewJob`, компенсация `_fail_unenqueued`.
  Вторая половина читает из события `pr_number`, `head_sha`, `action`, `installation_id`,
  `repo_full_name`, `repo_provider_id`.
- `find_reviewed` фильтрует `trigger = webhook`; вызывают его дубль вебхука и путь упавшего прогона
  (`app/application/review_pipeline.py`: было ли ревью head до сбоя). Пересчёт gate по треду
  (`refresh_merge_gate_for_merge_request`) берёт `find_latest` — прогон любого источника.
- `pause_check(repository)` (`app/application/merge_gate_publish.py`) с #329 вызывают вебхук PR,
  вебхук треда, `settle_merge_gate` и упавший прогон: на паузе каждый ставит check паузы вместо
  итога прогона.
- `PRMetadata` (`app/domain/entities.py`) не несёт ни форка, ни `author_association`;
  `GitHubVcsGateway.fetch_pr_metadata` их не читает. `VcsNotFoundError` на `404` уже есть.
- Права: `_manage_refusal` в `app/application/use_cases/connect_repository.py` —
  `catalog.user_repository(token, provider_id)` через `call_with_user_token`, правило `can_connect`.
  Явное право — `has_explicit_access`, видимость — `visible_for_connect`
  (`app/domain/repository_access.py`).
- Словари `responses` для всех отказов уже есть в `app/api/errors.py`:
  `ERRORS_403_ORIGIN_OR_MANAGE`, `ERRORS_404_REPOSITORY_OR_NOT_IN_INSTALLATION`,
  `ERRORS_409_REVIEW_ACTIVE`, `ERRORS_413_BODY`, `ERRORS_422`, `ERRORS_502_SCM`.
- Модель `RepositoryManageRequiredProblem` несёт константу `title` «Нужны права admin или maintain
  на репозиторий» — она в экспорте схемы (`const`) и в описании `ERRORS_403_ORIGIN_OR_MANAGE`.
- Из PR #308: `InstallationCatalog.repository_installation_id(full_name)` с кэшем,
  `Container.cabinet_vcs_gateway()` (20 с), `RepositoryNotInstalled`. Из PR #314: `pause_check`.
  Оба слиты в `develop`; колонка `repositories.installation_id` не заводится (ответ в PR #239).
- Воркер `ReviewJob.event_type` и `action` не читает: их только сериализует адаптер очереди
  (`app/infrastructure/queue/rabbitmq.py`). `trigger` прогона нигде не ветвит поведение, кроме
  `find_reviewed`.
- Активный change `repositories-one-list` правит в `review-rest-api` требования списка,
  подключения, видимости и потока событий. Этот change в `review-rest-api` только добавляет
  требования и удаляет «Запланированная ручка отвечает 501». Общих требований нет; оба меняют
  описание поведения `REPOSITORY_MANAGE_REQUIRED` в разных местах — при архиве второго проверить,
  что сценарии подключения не называют старый `title`.

## Goals / Non-Goals

**Goals:**

- Ручной запуск по опубликованному контракту: формы `CreateReviewRequest` и `ReviewJob` не
  меняются, новых кодов ошибок нет.
- Один путь записи и постановки для вебхука и ручного запуска.
- После change в контракте нет `501`, кроме `PROVIDER_NOT_SUPPORTED`.

**Non-Goals:**

- Изменение дубля вебхука: путь вебхука остаётся как есть (D4).
- `Idempotency-Key` у ручного запуска: повтор защищает `409 REVIEW_ALREADY_ACTIVE`, а повтор
  после завершения — желаемое поведение.
- Применение `branch_filter` (#313) — ни в вебхуке, ни в ручном запуске.

## Decisions

### D1. Общая половина «записать прогон и поставить задачу»

Вторая половина `handle_webhook_event` выносится в
`start_review_run(uow, queue, repository, launch, metadata, *, find_duplicate, now, new_id,
run_outcome) -> RunStart` (`app/application/use_cases/start_review_run.py`). `launch` —
`RunLaunch` (frozen dataclass): `trigger`, `action`, `pr_number`, `head_sha`, `installation_id`,
`repository_full_name`, `repository_provider_id`. `RunStart.kind` — `duplicate | conflict |
success` с `run`, `merge_request`, `repository`. `find_duplicate(work, merge_request_id, head_sha)`
— правило дубля вызывающего: вебхук — `find_active or find_reviewed` (как сейчас), ручной запуск —
`find_active`. Разбор гонки, повтор поверх записей победителя и `_fail_unenqueued` переезжают как
есть. `handle_webhook_event` оставляет себе первую половину и переводит `RunStart` в
`WebhookOutcome` один к одному.

- В реализации `start_review_run` принимает и `vcs`, а `RunStart.kind` — ещё `failure`: блокировка
  head при смене целевой ветки (#222) идёт посреди записи и переехала вместе с ней. Срабатывает
  только при `action = edited`; ручной запуск её не вызывает и `failure` не получает.
- Альтернатива: ручной use case пишет прогон сам — две копии разбора гонки и компенсации,
  именно той логики, которая уже ломалась (#75, #149).
- Альтернатива: флаг `allow_rerun: bool` вместо функции дубля — равноценно; функция оставляет
  правило рядом с вызывающим и не плодит ветку внутри.

### D2. Use case ручного запуска и порядок проверок

`launch_review(request, *, catalog, installations, vcs, uow, queue, user, now, new_id,
run_outcome) -> LaunchOutcome` (`app/application/use_cases/launch_review.py`). Исход —
`started(RunStart)` или отказ `LaunchRefusal` (StrEnum: `REPOSITORY_NOT_FOUND`,
`MANAGE_REQUIRED`, `NOT_INSTALLED`, `PULL_REQUEST_NOT_FOUND`, `PULL_REQUEST_NOT_OPEN`,
`PULL_REQUEST_UNTRUSTED`, `ALREADY_ACTIVE`). Отказы use case различает все: в HTTP несколько из них
сходятся в один код (D7), но тесты и лог видят причину. `VcsError` из шагов 1, 4, 5 идёт наверх →
`502`. Порядок — от дешёвого и не раскрывающего к дорогому:

1. `catalog.user_repository(token, provider_repository_id)` по токену пользователя мимо кэша
   видимости. `None` или `not visible_for_connect(found)` → `REPOSITORY_NOT_FOUND`.
2. `uow.repositories.find_by_provider(provider, provider_repository_id)`; `None` или
   `connected_at is None` → `REPOSITORY_NOT_FOUND`. GitHub спрошен до базы при любом id — ответ не
   выдаёт, зарегистрирован ли репозиторий (как `visible_repository`).
3. `not has_explicit_access(found.permissions)` → `MANAGE_REQUIRED`.
4. `installations.repository_installation_id(repository.full_name)`; `None` → `NOT_INSTALLED`.
5. `vcs.fetch_pr_metadata(full_name, number, installation_id)`; `VcsNotFoundError` →
   `PULL_REQUEST_NOT_FOUND`; `state != open` → `PULL_REQUEST_NOT_OPEN`;
   `not is_trusted_pull_request(metadata.from_fork, metadata.author_association)` →
   `PULL_REQUEST_UNTRUSTED` и запись в лог (поля как у вебхука).
6. `start_review_run` с `RunLaunch(trigger=MANUAL, action="manual", head_sha=metadata.head_sha, …)`
   и `find_duplicate = find_active`; `duplicate` и `conflict` → `ALREADY_ACTIVE`.

Установка, метаданные и `repository_full_name` задачи берут свежий `full_name` из ответа шага 1, а
не из записи в базе: имя в базе — со времени подключения, репозиторий могли переименовать.
Провайдер, отличный от `github`, — `REPOSITORY_NOT_FOUND` без обращения к GitHub. `launch_review`
возвращает `RunStart | LaunchRefusal`.

Кэш видимости не используется вовсе: свежий ответ GitHub одним запросом даёт и видимость, и
роль. Роль проверяется своим правилом над тем же ответом, `_manage_refusal` не трогаем.

- Альтернатива: сначала кэш видимости (`visible_provider_ids`), потом роль мимо кэша, как у
  `DELETE`. Два запроса вместо одного и тот же итог: свежий ответ строже кэша.

### D3. Правило «свой PR» — одна чистая функция

`is_trusted_pull_request(from_fork: bool, author_association: str | None) -> bool`
(`app/domain/webhook.py`) с `TRUSTED_AUTHOR_ASSOCIATIONS`; `is_trusted_author(event)` удаляется,
вебхук зовёт новую функцию полями события. `PRMetadata` получает обязательные `from_fork: bool` и
`author_association: str | None`; адаптер: `from_fork = (head.repo or {}).id != base.repo.id`
(удалённый форк — `head.repo = null` → форк), `author_association` — из тела PR. Ручной запуск
берёт оба поля из метаданных: события нет, а свежие данные точнее.

### D4. Дубль вебхука не меняется (решение пользователя) — известное ограничение

`find_reviewed` сохраняет фильтр `trigger = webhook`, путь вебхука не меняется. Следствие:
вебхук на коммит, уже проверенный вручную (задержанная доставка или переотправка проходом
воркера #149 после сбоя), создаёт ещё один прогон и публикует ещё одно ревью. Окно узкое: `opened`
ручному запуску не предшествует (PR уже есть), `synchronize` несёт новый коммит, так что
повторный вебхук на тот же коммит — только переотправка. Ограничение описывается в
`PIPELINE_SPEC.md` §4 и в описании ручки.

- Отвергнутая альтернатива: снять фильтр источника у `find_reviewed` — вебхук на проверенный
  вручную коммит становился бы дублем.

### D5. `auto_review_enabled = false` и `branch_filter` (решено)

Решение пользователя: ручной запуск работает при `auto_review_enabled = false` — настройка
управляет только автоматическим ревью по вебхуку, ручной запуск — явное действие человека с ролью.
`pause_check` в ручном пути не вызывается; выключенный репозиторий (`connected_at IS NULL`)
отсекается шагом 2 D2 как `404`. `branch_filter` не применяется: он не применяется нигде (#313), и
фильтр — про автоматический приём.

### D6. Сообщение очереди ручного прогона

`event_type = "pull_request"`, `action = "manual"`: формат §4.2 `SYSTEM_DESIGN.md` не меняется,
`action` отличает ручную постановку в DLQ и логах. Воркер по этим полям не ветвится (проверено:
читает их только адаптер очереди). `installation_id` — из шага 4 D2.

### D7. Отказы — существующими кодами (решение пользователя)

Новых кодов, моделей и словарей `responses` нет. Отображение `LaunchRefusal` → ответ:

| Отказ | Ответ | Почему так |
|---|---|---|
| `REPOSITORY_NOT_FOUND` | `404 REPOSITORY_NOT_FOUND` | одно правило для невидимого, незарегистрированного и выключенного |
| `MANAGE_REQUIRED` | `403 REPOSITORY_MANAGE_REQUIRED` | код отказа по роли на репозитории уже есть; заголовок обобщается (ниже) |
| `NOT_INSTALLED` | `404 REPOSITORY_NOT_IN_INSTALLATION` | как у диффа прогона и статуса правил |
| `PULL_REQUEST_NOT_FOUND` | `404 REPOSITORY_NOT_FOUND`, `detail` «Запроса на изменение с таким номером в репозитории нет.» | см. ниже |
| `PULL_REQUEST_NOT_OPEN` | `422 VALIDATION_ERROR`, `errors: [{where: body.pull_request_number, reason: «запрос на изменение закрыт или смёржен»}]` | решение пользователя |
| `PULL_REQUEST_UNTRUSTED` | `422 VALIDATION_ERROR`, `errors: [{where: body.pull_request_number, reason: «запрос на изменение из форка от автора не из участников репозитория»}]` | решение пользователя; лог — с `author_association` |
| `ALREADY_ACTIVE` | `409 REVIEW_ALREADY_ACTIVE` | код ровно для этого случая уже в каталоге |
| `VcsError` | `502 SCM_UNAVAILABLE` | как у вебхука и кабинета |
| сбой постановки | `500 INTERNAL_ERROR`, прогон `failed` | как `5xx` у вебхука |

`responses` ручки: `ERRORS_403_ORIGIN_OR_MANAGE`, `ERRORS_404_REPOSITORY_OR_NOT_IN_INSTALLATION`,
`ERRORS_409_REVIEW_ACTIVE`, `ERRORS_413_BODY`, `ERRORS_422`, `ERRORS_502_SCM`; без
`ERRORS_501_NOT_IMPLEMENTED`. Тело `422` собирается той же моделью `ValidationProblem`, что у
проверки тела: `reason` — постоянный текст, без значения из запроса. Для этого `ApiError`
получает необязательный `errors=`, который уходит в `problem_response` — второго пути сборки тела
нет.

**PR не найден — `REPOSITORY_NOT_FOUND`.** Из существующих `404` кодов по сущности
(`REVIEW_NOT_FOUND`, `REPOSITORY_NOT_FOUND`, `REPOSITORY_NOT_IN_INSTALLATION`, `ROUTE_NOT_FOUND`,
`PROVIDER_NOT_SUPPORTED`) ближе всего `REPOSITORY_NOT_FOUND`: цель запроса — пара «репозиторий +
номер PR» в нём, и `404` GitHub на `/pulls/{n}` установке означает и «нет PR», и «репозиторий
недоступен установке». Различает случаи `detail`. `REPOSITORY_NOT_IN_INSTALLATION` неверен —
установка проверена шагом 4, `REVIEW_NOT_FOUND` — про прогон. Отвергнуто: `422 VALIDATION_ERROR`
с `where = body.pull_request_number` — точнее по смыслу (как закрытый PR), но пользователь задал
`404`, и 2026-11-03 подтвердил выбор.

**Заголовок `REPOSITORY_MANAGE_REQUIRED` обобщается.** Константа `title` «Нужны права admin или
maintain на репозиторий» неверна для ручного запуска, где хватает push или triage. Выбрано
переименовать в «Недостаточно прав на репозиторий» и называть нужные роли в `detail` каждого
отказа: клиент ветвится по `code`, а не по `title` (`PIPELINE_SPEC.md` §9), и заголовок, который
врёт пользователю с ролью push, удивляет больше, чем смена константы. Это **изменение контракта**:
в экспорте меняется `const` `title` модели, описание словаря `ERRORS_403_ORIGIN_OR_MANAGE` и
докстринг модели; `code` и статус прежние. Отвергнуто: оставить заголовок и объяснять в
описании ручки — `title` в ответе ручного запуска противоречил бы ему.

### D8. Проекция `ReviewJob` — общая

`review_job(run, merge_request, repository) -> ReviewJob` переезжает из `app/api/webhooks.py` в
`app/api/schemas.py` рядом с моделью; `webhooks.py` и `reviews.py` зовут её. Use case форму
контракта по-прежнему не знает.

### D9. `trigger` в теле — схема прежняя, проверка в рантайме (решение пользователя)

`CreateReviewRequest.trigger` остаётся `TriggerSource` с `Field(strict=False)`; в схеме по-прежнему
три значения. Значение, отличное от `manual`, отклоняет `field_validator` модели
(`PydanticCustomError`, `reason` «принимается только manual»): ответ — стандартный `422
VALIDATION_ERROR` с `where = body.trigger` из общего обработчика проверки тела, до обращения к
GitHub. Описание поля в схеме говорит, что принимается только `manual`. Альтернатива — отказ в
теле ручки через `ApiError` — дала бы второй путь сборки `errors`.

### D10. Удаление требования о заглушке и сверка каталога

Требование «Запланированная ручка отвечает 501» — `REMOVED` (сценарии без заглушки
невыполнимы; правило остаётся в `AGENTS.md` и `backend.md`). В
`tests/test_problem_details.py`: `test_planned_endpoint_answers_not_implemented` удаляется,
`test_planned_endpoint_checks_origin_session_and_body_first` переименовывается под реальную ручку,
в `test_server_bodies_match_their_models` добавляется настоящий ответ `REVIEW_ALREADY_ACTIVE`,
исключение переходит на `NOT_IMPLEMENTED`.

### D11. Merge gate ручного прогона не перекрывается паузой авторевью (решение пользователя)

Пауза `auto_review_enabled = false` — про автоматическое ревью. С #329 на паузе check паузы
ставят все четыре пути, и ручной прогон при выключенном авторевью терял свой gate: `critical`
не блокировал мердж ни сразу, ни после закрытия треда. Решение — правило в одном месте:

```
pause_check(repository, head_run: ReviewRun | None) -> MergeGateCheck | None
  connected_at is None                                   -> DISABLED_REVIEW_CHECK
  not auto_review_enabled:
    head_run.trigger is MANUAL and status not in {FAILED, CANCELLED} -> None
    иначе                                                -> AUTO_REVIEW_OFF_CHECK
  иначе                                                  -> None
```

- `head_run` — последний прогон на head (`find_latest(merge_request_id, head_sha)`); каждый вызов
  его уже читает или читает в том же `with uow`: `settle_merge_gate` — `latest`, упавший прогон —
  `latest`, вебхук треда — новое чтение, вебхук PR — прогон на head события (на новом коммите его
  нет, поведение прежнее).
- Повторное событие PR на head ручного прогона при `auto_review_enabled = false` (переотправка
  `opened`/`synchronize`, `edited`): `pause_check` даёт `None`, а дубль вебхука ручной прогон не
  видит (D4), поэтому вебхук PR при выключенном авторевью отвечает `ignored` без прогона и без
  check — gate ручного прогона остаётся. Иначе на паузе запускалось бы автоматическое ревью.
- Ручной прогон в работе (`queued`/`running`) на паузе: вебхук треда идёт в
  `refresh_merge_gate_for_merge_request`, который незавершённый head не пересчитывает — gate
  выставит сам прогон.
- Упавший ручной прогон на паузе — check паузы (`success`): `failure` держал бы PR, который
  автоматическое ревью не ждёт (логика #329); пользователь перезапускает ревью руками.
- Выключенный репозиторий важнее ручного прогона: ручной запуск его и так не создаёт (D2, шаг 2),
  а начатый до выключения прогон не должен блокировать.
- Альтернатива: проверка источника в каждом из четырёх вызовов — четыре копии правила, именно
  то, что #329 свёл в `pause_check`.

## Risks / Trade-offs

- [Задержанный вебхук после ручного прогона запускает ещё одно ревью (D4)] → узкое окно
  (только переотправка доставки на тот же коммит); описано в `PIPELINE_SPEC.md` §4.
- [Смена `title` у `REPOSITORY_MANAGE_REQUIRED` — изменение контракта] → `code` и статус прежние;
  фронтенд перегенерирует типы, в описании PR — отдельной строкой.
- [`404 REPOSITORY_NOT_FOUND` на отсутствующий PR сбивает клиента, который показывает текст по
  коду] → `detail` называет PR; Open Questions.
- [Один запрос к GitHub по токену пользователя на каждый запуск, без кэша] → запуск редкий и
  ручной; `401` и обновление токена — общий `_as_user`.
- [`PRMetadata` с новыми обязательными полями ломает фикстуры воркера] → правка фейков
  (`tests/fakes/*`), без новых модулей.

## Migration Plan

Схема базы не меняется (`trigger_source` уже содержит `manual`), миграции нет. Откат — revert PR:
заглушка вернётся, прогоны `manual` в базе останутся читаемыми.

## Open Questions

Решены 2026-11-03:

- ~~Merge gate после ручного прогона при `auto_review_enabled = false`~~ — исправляется в этом
  change, D11.
- ~~Код отказа на отсутствующий PR~~ — остаётся `404 REPOSITORY_NOT_FOUND` с `detail` про PR (D7).

Исходные формулировки:

- **Для пользователя: merge gate после ручного прогона при `auto_review_enabled = false`.** Ручной
  прогон публикует настоящий merge gate, но следующий вебхук закрытия треда
  (`apply_review_thread_webhook`, `pause_check` → `AUTO_REVIEW_OFF_CHECK`) ставит поверх проверку
  `success` «Авторевью выключено в настройках» и gate не пересчитывает — `critical` от ручного
  прогона перестаёт блокировать слияние. Change это не меняет. Предложение: открыть задачу
  (`gh issue create`) — при `AUTO_REVIEW_OFF_CHECK` пересчитывать gate, если на head есть
  завершённый ручной прогон; на `DISABLED_REVIEW_CHECK` — оставить как есть.
- **Для пользователя: код отказа на отсутствующий PR.** Выбран `404 REPOSITORY_NOT_FOUND` с
  `detail` про PR (D7). Если точность важнее статуса — `422 VALIDATION_ERROR` с
  `where = body.pull_request_number`, как у закрытого PR; меняются один сценарий и одна строка
  таблицы D7.
