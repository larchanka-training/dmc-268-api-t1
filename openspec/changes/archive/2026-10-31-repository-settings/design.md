## Context

Мотивация — `proposal.md`, поведение — дельты в `specs/`. Что есть сейчас:

- Заглушки `get_repository_settings`, `save_repository_settings`, `get_repository_rules` в
  `app/api/repositories.py` с полными моделями (`RepositorySettingsResponse`,
  `RepositorySettingsUpdate`, `RulesStatusResponse` с пятью вариантами) отвечают `501`. Описания —
  `_GET_SETTINGS_DESCRIPTION`, `_SAVE_SETTINGS_DESCRIPTION`, `_RULES_DESCRIPTION`,
  `_BRANCH_FILTER_DESCRIPTION`. В `responses` нет `502`, хотя видимость (`visible_repository`) и
  роль (`_as_user` + `connecting.disconnect_repository`) могут дать `502 SCM_UNAVAILABLE`.
- `repositories.auto_review_enabled` — `NOT NULL` без `server_default` (ревизия `0001`), в модели
  `default=False`; оба пути подключения (`connect_repository`, `connect_installation`) пишут
  `True`, `reconnect` колонку не трогает. Значит, у всех живых записей сейчас `true`, а умолчание
  ORM расходится с умолчанием контракта (`true`).
- Выключенность репозитория — `connected_at IS NULL`; её проверяют два места:
  `handle_webhook` (→ `_pass_merge_gate_of_disabled`, check `DISABLED_REVIEW_CHECK`) и
  `apply_review_thread_webhook` в `app/application/sync_thread_resolution.py`.
- Merge gate: `merge_blocked_by_findings(open_findings)` (`app/domain/merge_gate.py`) сравнивает с
  `CRITICAL`; `build_merge_gate_check` (`app/application/merge_gate_publish.py`) зовут
  `publish_review` (воркер) и `refresh_merge_gate_for_merge_request` (вебхук треда). Ранг важности
  уже есть — `_RANK` в `app/domain/thread_coverage.py`.
- `load_repo_rules(repo_full_name, base_sha, installation_id, *, vcs, cache, max_bytes,
  cache_ttl_seconds)` (`app/application/repo_config_loader.py`) кэширует `{"status", "text"}` по
  `repo-rules:v1:{full_name}:{sha}`; времени получения нет, `RepoRules` его не несёт.
- PR #308 (база этой ветки) дал `InstallationCatalog.repository_installation_id(full_name)` с
  кэшем, `Container.cabinet_vcs_gateway()` (тайм-аут 20 с) и образец use case кабинета —
  `app/application/use_cases/run_diff.py`.
- Активный change `repositories-one-list` правит в `review-rest-api` другие требования, а в
  `webhook-intake` (`Незарегистрированный репозиторий игнорируется`) и `review-data-model`
  (`Регистрация репозитория`) — существующие. Этот change в тех двух спеках только добавляет
  требования, пересечения по требованиям нет. Ревизия `0014` (`connected_at`) — его; наша — `0016`: `0015` занял `redact-context-payloads`.

## Goals / Non-Goals

**Goals:**

- Три ручки по опубликованному контракту, без изменения форм тел.
- Хранение настроек одной миграцией, безопасной для окна деплоя.
- `auto_review_enabled` и `min_severity` реально действуют; `branch_filter` честно описан как
  неприменяемый.

**Non-Goals:**

- Применение `branch_filter` в вебхуке — отдельная задача (группа 7 в `tasks.md`).
- Пересчёт стоящего check run при смене `min_severity` — порог действует со следующего прогона
  или вебхука закрытия треда.
- Порог в правиле покрытия тредов (`thread_coverage`: «вернувшийся `critical` публикуется
  поверх закрытого треда ниже») — остаётся про `critical`.
- Общий кэш правил между API и воркером — кэш в памяти процесса, как сейчас.

## Decisions

### D1. Настройки — колонки `repositories`, не отдельная таблица

`branch_filter text[] NOT NULL DEFAULT '{}'` и `min_severity finding_severity NOT NULL DEFAULT
'critical'` в той же записи, рядом с `auto_review_enabled` (контракт прямо говорит «та же
колонка»). «Без сохранённых настроек» = значения по умолчанию колонок: отдельного признака
«сохраняли или нет» нет — ответ GET от него не зависит.

- Альтернатива: таблица `repository_settings` 1:1 — второй источник для `auto_review_enabled`
  или его перенос; лишний JOIN на вебхуке. Отклонено.
- Альтернатива: `jsonb` для `branch_filter` — нет выигрыша, `text[]` проще маппится в tuple.
- Enum — существующий тип `finding_severity`: `postgresql.ENUM(name='finding_severity',
  create_type=False)`; в модели — тот же `_enum(FindingSeverity, "finding_severity")`, что у
  находок, чтобы `tests/db/test_drift.py` не видел расхождения.

### D2. `auto_review_enabled` получает `server_default true`

Контракт обещает `true` по умолчанию, а модель держит `default=False`. В `0016`:
`ALTER COLUMN auto_review_enabled SET DEFAULT true`; в модели — `server_default=true()` вместо
`default=False`. Данные не трогаем: у всех записей уже `true` (оба пути подключения пишут его).
Откат — `DROP DEFAULT`.

- Альтернатива: оставить как есть — запись без значения (ручная вставка, будущий путь
  регистрации) молча выключила бы авторевью вопреки контракту.

### D3. Миграция — короткий DDL, без переноса данных

`ADD COLUMN … NOT NULL DEFAULT <константа>` в PostgreSQL ≥ 11 — только каталог, без перезаписи
таблицы; `SET DEFAULT` — тоже. Таблица маленькая; блокировка `ACCESS EXCLUSIVE` короткая и
укладывается в `lock_timeout = 5s`. Старый API в окне деплоя вставляет строки без новых колонок —
их заполняют `server_default`. Новый API на старой схеме не работает (колонок нет) — порядок
деплоя прежний: миграция до нового API.

### D4. Порог merge gate — чистая функция с порогом аргументом

`merge_blocked_by_findings(open_findings, min_severity)`: блокирует находка с рангом не ниже
порога. Ранг — один словарь в домене (`_RANK` переезжает из `thread_coverage.py` в общий
`severity_at_least(severity, threshold)` в `app/domain/merge_gate.py` или `enums.py`, без копии).
`build_merge_gate_check(open_findings, min_severity)` называет порог в заголовке и тексте:
«Есть незакрытые замечания уровня {порог} и выше» / «Ревью пройдено». Порог берётся из записи
репозитория: в воркере — чтение репозитория в той же транзакции, что `collect_open_findings`
(по `merge_request.repository_id`); в вебхуке треда — из уже прочитанного `repo`.

- Альтернатива: класть порог в сообщение очереди (`ReviewJob`) — смена порога после постановки не
  действовала бы, а формат сообщения меняется для старого воркера. Отклонено.
- Заголовок при `critical` меняется с «Есть критичные замечания» на общий шаблон — видимое
  изменение текста check run; формулировки в спеках его не фиксируют.

### D5. `auto_review_enabled = false` — тот же путь, что у выключенного, со своим текстом

Решение «ревью по вебхуку не нужно» несёт причину: функция
`pause_check(repository) -> MergeGateCheck | None` в `app/application/merge_gate_publish.py`,
рядом с текстами check'ов, сразу отдаёт check run для head; `None` — ревью идёт. Выключение
репозитория важнее настройки: при обоих признаках — `DISABLED_REVIEW_CHECK`. Её зовут два места
(`handle_webhook`, `apply_review_thread_webhook`): `connected_at is None` →
`DISABLED_REVIEW_CHECK` («Ревью выключено в сервисе»), `auto_review_enabled = false` → новый
`AUTO_REVIEW_OFF_CHECK` — `success`, заголовок «Авторевью выключено в настройках», текст:
«Автоматическое ревью выключено в настройках репозитория в кабинете; проверка мёрж не блокирует.»
`_pass_merge_gate_of_disabled` принимает check аргументом.

- Альтернатива: общий текст «Ревью выключено в сервисе» для обоих — проще, но пользователь не
  поймёт, где включать обратно: выключение лечится подключением, настройка — экраном настроек.
  Отклонено (решение пользователя).
- Альтернатива: `bool`-предикат и выбор текста по полям в двух местах — правило приоритета
  повторилось бы дважды. Отклонено.
- Альтернатива: при `auto_review_enabled = false` вебхук треда пересчитывает gate — тогда
  выключение не снимает `failure`, хотя новых ревью не будет и замечания не обновятся. Отклонено:
  поведение должно совпадать с выключением.
- Дополнительного чтения базы нет: `handle_webhook` уже читает репозиторий; признак — в той же
  строке.

### D6. Use case настроек — рядом с `disconnect_repository`

`save_repository_settings(catalog, uow, user, repository, settings, now) -> ConnectRefusal | None`
в `app/application/use_cases/connect_repository.py` (или своём модуле, если файл перерастёт):
роль — тем же `call_with_user_token` + `catalog.user_repository` + `can_connect`, что у
выключения; потом `uow.repositories.update_settings(repository_id, settings, now)` — новый метод
порта, `UPDATE` трёх колонок и `updated_at`. Значение — доменный `RepositorySettings`
(frozen dataclass: `auto_review_enabled`, `branch_filter: tuple[str, ...]`, `min_severity`);
`Repository` получает поля `branch_filter` и `min_severity`. Идемпотентность — естественная:
полная замена тем же значением даёт ту же строку (меняется только `updated_at`).

- Альтернатива: расширить `RepositoryRepo.update` — его зовут пути подключения, и запись настроек
  там затёрла бы сохранённое пользователем. Отклонено.
- Чтение настроек GET — из той же сущности, что вернул `visible_repository`; второго запроса нет.

### D7. Статус правил — use case поверх `load_repo_rules`

`load_default_branch_rules(repository, *, installations, vcs, cache, max_bytes, ttl_seconds, now)
-> RepoRules` в `app/application/use_cases/repository_rules.py`:

1. `installations.repository_installation_id(full_name)` (механизм #87, с кэшем); `None` →
   исключение `RepositoryNotInstalled` — то же, что у диффа прогона (`app/application/use_cases/run_diff.py`;
   переезжает в общий модуль use case'ов кабинета, если импорт из `run_diff` окажется странным),
   ручка отвечает `404 REPOSITORY_NOT_IN_INSTALLATION`. `VcsError` запроса установки →
   `unavailable`.
2. Новый метод порта `VcsGateway.resolve_branch_sha(repo_full_name, branch, installation_id) ->
   str` — адаптер GitHub: `GET /repos/{full_name}/commits/heads/{branch}` с
   `Accept: application/vnd.github.sha` (тело — sha текстом). Префикс `heads/` — форма ветки из
   документации «Get a commit»: имя ветки не спутать с тегом или sha; `/` в имени остаётся как
   есть, прочее экранируется. Ветки нет — GitHub отвечает `422` «No commit found for SHA», а не
   `404` (проверено вживую), — обычный `VcsError`; репозитория нет или он недоступен установке —
   `404`, `VcsNotFoundError`. Любой `VcsError`, оба случая тоже, → `unavailable`, без кэша.
3. `load_repo_rules(full_name, sha, installation_id, …, now=now)`.

Ветка и имя — текущие из ответа GitHub, который уже получен для проверки видимости (как в
`GET /repositories`), а не сохранённые при подключении: вебхука `repository` у приложения нет, и
смену или переименование ветки по умолчанию база не видит. Шлюз — `cabinet_vcs_gateway()` (20 с),
кэш — `Container.rules_cache()` процесса API (своё хранилище, чтобы не вытеснять видимость), лимиты — те же `rules_max_bytes` и
`rules_cache_ttl_seconds`, что у воркера. Ответ API собирается из `RepoRules` в слое API.

- Альтернатива: читать файл по имени ветки без разрешения в sha — кэш по ветке пришлось бы
  инвалидировать по времени, и «изменилось ли» не узнать. Отклонено (так и в задаче).
- Альтернатива: на сбой — `502 SCM_UNAVAILABLE`. Отклонено: в контракте уже есть `unavailable`
  как исход «провайдер не ответил», ревью в этом случае тоже идёт по стандартным критериям.
- Альтернатива: неустановленное приложение — тоже `unavailable` (без изменения контракта).
  Отклонено (решение пользователя): это не сбой, а состояние, которое пользователь исправляет
  установкой; ответ совпадает с диффом прогона.
- Метод порта оправдан вызывающим (use case) и существующим адаптером; второй реализации нет, но
  `VcsGateway` — порт чужого внешнего сервиса, мок в тестах — `FakeVcs`.

### D8. `fetched_at` — в значении кэша, ключ `repo-rules:v2`

`RepoRules` получает `fetched_at: datetime | None` (`None` у `unavailable`). `load_repo_rules`
принимает `now` аргументом (чистое ядро: время приходит снаружи) и пишет
`{"status", "text", "fetched_at"}`; попадание отдаёт сохранённое время. Префикс меняется на `v2`:
запись `v1` без времени не читается — один промах на коммит после деплоя, а не время чтения вместо
времени получения. Воркер передаёт `now` из своего вызова; поведение прогона не меняется.

- Альтернатива: читать `v1` и подставлять `now` — `fetched_at` врал бы. Отклонено.

### D9. Контракт: формы те же, отказы дополнены

Модели запросов и ответов не меняются. Изменения в схеме:

- у трёх ручек уходит `ERRORS_501_NOT_IMPLEMENTED`, добавляется `ERRORS_502_SCM_VISIBILITY`;
- у `GET …/rules` `404` — объединение `REPOSITORY_NOT_FOUND | REPOSITORY_NOT_IN_INSTALLATION` по
  `code`: новый словарь `ERRORS_404_REPOSITORY_OR_NOT_IN_INSTALLATION` в `app/api/errors.py` по
  образцу `ERRORS_404_REVIEW_OR_NOT_IN_INSTALLATION` (ключ статуса в `responses` один, два словаря
  затёрли бы друг друга). Новых кодов и моделей ошибок нет;
- описания ручек — реальное поведение вместо «Не реализовано»;
- `_BRANCH_FILTER_DESCRIPTION` дописывается: «Пока не применяется: сохраняется, но ревью идёт для
  всех веток (задача #313)».

Порядок проверок ручки `PUT`: `Origin` (middleware) → сессия → тело и id (`422`) → видимость по
кэшу (`404`) → роль у GitHub мимо кэша (`403`) → запись. GET — сессия → id → видимость.

## Risks / Trade-offs

- [Пользователь сохранил `branch_filter`, а ревью идёт для всех веток] → описание поля в
  контракте говорит об этом прямо; задача на применение заводится в этом же PR (7.1); фронтенд
  может показать подсказку по описанию.
- [Смена `min_severity` не меняет стоящий check run] → зафиксировано сценарием; пересчёт — на
  следующем прогоне или закрытии треда. Если понадобится сразу — отдельная задача.
- [Воркер на публикации читает репозиторий — лишний запрос к базе] → один `SELECT` по PK в уже
  открытой транзакции `collect_open_findings`; вебхук лишнего чтения не получает (D5).
- [`GET …/rules` ходит в GitHub на каждый запрос (разрешение ветки) и до 20 с ждёт] → файл и номер
  установки кэшируются; разрешение ветки — один лёгкий запрос; сбой — `unavailable` без `502`.
  Лимит запросов installation-токена общий с воркером — кабинет зовёт ручку при открытии экрана,
  не в цикле.
- [Текст check run при `critical` меняется] → видимо только на GitHub; спеки текст не фиксируют.
- [Контракт `GET …/rules` получает второй код `404`] → добавочное изменение; фронтенд уже
  обрабатывает `REPOSITORY_NOT_IN_INSTALLATION` у диффа прогона.
- [Кэш правил после деплоя пуст (`v2`)] → по одному лишнему запросу файла на коммит.
- [Новый API на старой схеме] → миграция идёт до выката API (порядок деплоя не меняется).

## Migration Plan

1. Ревизия `0016` (`down_revision = '0015'`): `ADD COLUMN branch_filter`, `ADD COLUMN
   min_severity`, `ALTER COLUMN auto_review_enabled SET DEFAULT true`. Откат — `DROP COLUMN` ×2 и
   `DROP DEFAULT`; сохранённые настройки при откате теряются (кроме `auto_review_enabled`).
2. Деплой обычный: миграция до API и воркера; старые API и воркер работают на новой схеме
   (новые колонки им не нужны, вставки получают умолчания).
3. Откат кода без отката схемы безопасен: старый код новые колонки не читает.

## Решённые вопросы

- Текст check run при `auto_review_enabled = false` — свой, «Авторевью выключено в настройках»
  (D5).
- Вебхук закрытия треда при `auto_review_enabled = false` записывает тред и ставит на head
  проверку паузы, gate не пересчитывает (D5).
- Заголовок check run merge gate при пороге `critical` — общий шаблон с названием порога (D4).
- Признака «настройки ни разу не сохраняли» нет: контракт его не содержит, GET отдаёт значения
  колонок (D1).
- Приложение не установлено — `GET …/rules` отвечает `404 REPOSITORY_NOT_IN_INSTALLATION` (D7, D9).
