## Why

Кабинет удаляет моки (dmc-268-ui-t1#40), когда в контракте не останется заглушек
`NOT_IMPLEMENTED`. `POST /api/v1/reviews` — последняя: схема запроса и ответа опубликована, а
ручка отвечает `501`, и запустить ревью можно только вебхуком. Без ручного запуска нельзя
повторить ревью проверенного коммита и нельзя получить ревью, когда вебхук не пришёл или
автоматическое ревью выключено в настройках. Задача #273.

## What Changes

- `POST /api/v1/reviews` создаёт прогон с `trigger = manual` и ставит задачу в очередь:
  `202` с `ReviewJob` той же формы, что у приёма вебхука.
- Репозиторий — по паре `provider` + `provider_repository_id`; невидимый пользователю,
  отсутствующий и выключенный (`connected_at IS NULL`) — один ответ `404 REPOSITORY_NOT_FOUND`.
- Право запуска — роль push, triage, maintain или admin у GitHub, проверенная запросом мимо кэша
  видимости. Без роли — существующий `403 REPOSITORY_MANAGE_REQUIRED`; его постоянный `title`
  обобщается до «Недостаточно прав на репозиторий», роли называет `detail`.
- Номер установки приложения — тем же кэшируемым механизмом, что у диффа прогона; не установлено —
  `404 REPOSITORY_NOT_IN_INSTALLATION`.
- Свежие метаданные PR дают `head_sha`, `base_sha` и состояние: PR не найден —
  `404 REPOSITORY_NOT_FOUND` со своим `detail`; закрыт, смёржен или из форка от автора не из
  `OWNER`/`MEMBER`/`COLLABORATOR` (правило то же, что у вебхука) — `422 VALIDATION_ERROR` с
  `where = body.pull_request_number`. Прочий сбой GitHub — `502 SCM_UNAVAILABLE`. Новых кодов
  ошибок нет.
- Повтор ревью уже проверенного коммита разрешён: ручной запуск смотрит только на активный
  прогон. Активный прогон на этот коммит, в том числе выигравший гонку, —
  `409 REVIEW_ALREADY_ACTIVE`.
- Запись и постановка — общий с вебхуком путь: запись, коммит, задача; сбой постановки переводит
  прогон в `failed`, ответ — `500`.
- Ручной запуск работает и при `auto_review_enabled = false` (настройка — про автоматическое
  ревью) и не применяет `branch_filter`.
- `trigger` тела принимает только `manual`; схема поля остаётся `TriggerSource`, остальные
  значения отклоняются при проверке тела `422 VALIDATION_ERROR`.
- Путь вебхука не меняется: задержанный вебхук на коммит, проверенный вручную, запустит ещё одно
  ревью — известное ограничение.
- Merge gate ручного прогона действует и при `auto_review_enabled = false`: пауза «Авторевью
  выключено в настройках» не перекрывает итог ручного прогона на head — ни при его завершении, ни
  при закрытии треда. Упавший ручной прогон на паузе оставляет check паузы. Выключенный
  репозиторий по-прежнему ставит «Ревью выключено в сервисе». Без этого `critical` ручного прогона
  не блокировал бы мердж: с #329 на паузе check паузы ставит и сам завершившийся прогон.
- Метаданные PR (`VcsGateway.fetch_pr_metadata`) несут `from_fork` и `author_association`;
  правило «свой PR» — одна чистая функция на вебхук и ручной запуск.
- Контракт: формы `CreateReviewRequest` и `ReviewJob` не меняются. Из `responses` ручки уходит
  `501`, добавляются `403` (`ORIGIN_FORBIDDEN` | `REPOSITORY_MANAGE_REQUIRED`), `404`
  (`REPOSITORY_NOT_FOUND` | `REPOSITORY_NOT_IN_INSTALLATION`) и `502 SCM_UNAVAILABLE`. Меняется
  константа `title` модели `REPOSITORY_MANAGE_REQUIRED`. После change в `docs/openapi/openapi.json`
  нет ни одного `501`, кроме постоянного `501 PROVIDER_NOT_SUPPORTED` у
  `POST /webhooks/{provider}`.
- Требование «Запланированная ручка отвечает 501» уходит из спеки `review-rest-api`: заглушек в
  контракте больше нет. Правило заглушки остаётся в `AGENTS.md` и `.agents/rules/backend.md`, код
  `NOT_IMPLEMENTED` — в каталоге для будущих заглушек.

## Capabilities

### New Capabilities

Нет.

### Modified Capabilities

- `review-rest-api`: ручной запуск ревью — новые требования (запуск, право, отказы по PR,
  повтор проверенного коммита, гонка, сбой постановки, контракт), общий заголовок отказа
  `REPOSITORY_MANAGE_REQUIRED`; удалено требование «Запланированная ручка отвечает 501».
- `review-pipeline`: требование «Прогон при паузе ревью не блокирует merge gate» — пауза
  `auto_review_enabled = false` не перекрывает merge gate ручного прогона на head.

## Impact

- Код: `app/domain/webhook.py` (правило «свой PR» без `WebhookEvent`), `app/domain/entities.py`
  (`PRMetadata`), `app/infrastructure/vcs/github.py` (`fetch_pr_metadata`),
  `app/application/use_cases/handle_webhook.py` (общая половина записи и постановки), новые use
  case'ы `start_review_run` и `launch_review`, `app/api/reviews.py`, `app/api/webhooks.py` и
  `app/api/schemas.py` (проекция `ReviewJob` — общая), `app/api/problems.py` и `app/api/errors.py`
  (заголовок и описание `REPOSITORY_MANAGE_REQUIRED`).
- Контракт: `docs/openapi/openapi.json` — `responses` и описание `POST /api/v1/reviews`, описание
  поля `trigger`, `title` модели `REPOSITORY_MANAGE_REQUIRED`; формы тела и ответа прежние.
- Тесты: `tests/test_problem_details.py` — `NOT_IMPLEMENTED` больше не отдаёт ни одна ручка,
  исключение в сверке каталога переходит с `REVIEW_ALREADY_ACTIVE` на `NOT_IMPLEMENTED`.
- Документация: `docs/pipeline/PIPELINE_SPEC.md` §4 (решённые вопросы и ограничение) и §9 (ручка
  реализована, есть `GET /reviews`), `docs/GITHUB_APP.md` (`403 REPOSITORY_MANAGE_REQUIRED` у
  ручного запуска).
- Схема базы не меняется: `trigger_source` уже содержит `manual`.
- Merge gate: `app/application/merge_gate_publish.py` (`pause_check` получает прогон на head),
  вызовы в `settle_merge_gate`, `app/application/review_pipeline.py` (упавший прогон),
  `app/application/sync_thread_resolution.py` (вебхук треда) и `handle_webhook.py` (вебхук PR).
- Опирается на слитые #308 (установка приложения в API), #314 (настройки и `pause_check`) и #329
  (check паузы при завершении прогона).
