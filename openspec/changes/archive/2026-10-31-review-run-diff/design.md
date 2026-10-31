## Context

Мотивация — `proposal.md`, требования — дельта `specs/review-rest-api/spec.md`. Что есть сейчас:

- Заглушка `get_review_diff` в `app/api/reviews.py` уже проверяет сессию и видимость
  (`Depends(visible_run)`) и отвечает `501`; модели `DiffLineResponse`, `DiffHunkResponse`,
  `DiffFileResponse`, `ReviewDiffResponse` описаны там же, описание — `_DIFF_DESCRIPTION`.
  `visible_run` уже читает репозиторий прогона (`_repository_of`), но наружу отдаёт только прогон.
- `VcsGateway.fetch_diff(repo_full_name, base_sha, head_sha, installation_id, *, max_bytes)`
  ходит в `GET /repos/{full_name}/compare/{base}...{head}` installation-токеном и обрывает тело на
  лимите (`VcsFileTooLargeError`); `404` — `VcsNotFoundError` из `app/infrastructure/vcs/errors.py`,
  слою приложения недоступный: импорт инфраструктуры запрещён `lint-imports`.
- `parse_diff` (`app/domain/diff_parser.py`) отдаёт `ParsedFile` (`entities.py`) — пути,
  `is_binary`, `is_deleted`, ханки с множествами изменённых строк и сырым `text`. Признака
  добавленного файла, строк по отдельности и счётчиков нет. Вызывающие — сборка контекста
  (`build_review_context`, `context_assembly` через `filter_diff_files`) и тесты.
- `installation_id` в API не известен: он есть в вебхуке и в сообщении очереди, в базе — нет.
  `GitHubInstallationCatalog.app_installed` уже делает `GET /repos/{full_name}/installation`
  с JWT приложения и разбирает `_Installation.id`, но возвращает только `bool`.
- `InMemoryCacheStore` ограничен числом записей (1024, FIFO с выметанием истёкших), один на
  контейнер; в API его делит кэш видимости (`CachedInstallationCatalog`, ключ — sha256 токена).
- VCS-шлюз API собирается с `API_VCS_LIMITS` (тайм-аут 5 с, лимит не пережидается) — под
  бюджет ответа вебхуку в 10 с. Контейнер API — 512 МБ, один процесс uvicorn, ручки — синхронные
  `def` в threadpool.
- Незаархивированный change `repositories-one-list` меняет (`MODIFIED`) требование «Набор
  видимых репозиториев кэшируется» и код `CachedInstallationCatalog` (`read=False`). Этот change
  те же требования не трогает: кэш установки — отдельным `ADDED`-требованием; порядок архивации
  двух change'ей не важен. Если `repositories-one-list` смёржится раньше — перебазироваться и
  сверить `CachedInstallationCatalog`, конфликт ожидается только текстовый.

## Goals / Non-Goals

**Goals:**

- Реальная ручка диффа без изменения формы ответа `200` и без миграций.
- Решение о форме диффа — чистая функция домена; ввод-вывод — в use case и адаптерах.
- Память API ограничена: и на один запрос (лимит размера диффа), и на кэш (лимит в байтах).

**Non-Goals:**

- Хранить `installation_id` в `repositories` (колонка обсуждается в #239).
- Подсветка синтаксиса, свёртка больших файлов, постраничная выдача диффа.
- Общий для реплик кэш (Redis) — у API одна реплика.
- Дифф для провайдеров кроме GitHub: прогон не с GitHub и так не виден.

## Decisions

### D1. Представление диффа — отдельная чистая функция над `ParsedFile`

Новый модуль `app/domain/diff_view.py`: неизменяемые `DiffLine` (`kind`, `old_line`, `new_line`,
`content`), `DiffHunkView` (`header`, старты и счётчики, `lines`), `DiffFileView` (`path`,
`previous_path`, `status`, `is_binary`, `language`, `additions`, `deletions`, `hunks`) и функция
`build_diff_view(files: Sequence[ParsedFile]) -> tuple[DiffFileView, ...]`. Перечисления
`DiffFileStatus` и `DiffLineKind` — в `app/domain/enums.py`.

- Строки ханка выводятся из `Hunk.text` (заголовок `@@` и тело как в диффе): `+` — добавленная,
  `-` — удалённая, `\` — служебная, пропускается, остальное — контекст; номера — от
  `old_start`/`new_start`, так же, как считает `_build_hunk`. `header` — текст после второго `@@`.
- Статус: `is_deleted` → `deleted`, `is_added` → `added`, `old_path` → `renamed`, иначе `modified`.
- `language` — `language_for_path(path)` из `app/domain/code_context.py` (5 языков), иначе `None`.
- У `ParsedFile` появляется поле `is_added: bool = False`: `_parse_file` ставит его по
  `new file mode` или `--- /dev/null`. Поле с умолчанием — существующие вызовы и конструкторы в
  тестах не ломаются.

Альтернативы: расширить `Hunk` строками — меняет тип, на котором держится сборка контекста и
привязка якорей; второй парсер для кабинета — два разбора одного формата, расходящиеся на краях
(кавычки git, переименования). Отвергнуты.

### D2. Номер установки — метод `InstallationCatalog`, кэш — в `CachedInstallationCatalog`

- Порт: `repository_installation_id(full_name: str) -> int | None` — `None`, если приложение не
  установлено (`404`) или установка приостановлена.
- `GitHubInstallationCatalog`: тот же `GET /repos/{full_name}/installation` с JWT, что у
  `app_installed`; `app_installed` переписывается через новый метод (`is not None`) — один запрос,
  одна ветка разбора.
- `CachedInstallationCatalog.repository_installation_id`: ключ `installation-id:v1:{full_name}`
  (без токена), значение — число строкой, TTL — новая настройка
  `INSTALLATION_ID_CACHE_TTL_SECONDS` (по умолчанию 300). `None` и сбои не кэшируются; сбой
  хранилища — промах. `app_installed` по-прежнему идёт мимо кэша (проверка подключения).
- Хранилище — общий `CacheStore` видимости: записи крошечные, их число ограничено числом
  репозиториев. Докстринг модуля («кэшируется только `list_repositories`») правится.
- Двойники: `FakeInstallationCatalog` получает `installation_ids: dict[str, int] | None`
  (по умолчанию — `1` для любого установленного `full_name`) и пишет вызов в `calls`; новых
  модулей с фейками не заводим.

Альтернативы: колонка `installation_id` (миграция и заполнение из вебхука) — вне решения D1-A
задачи; `GET /user/installations` токеном пользователя — постраничный обход ради одного числа.

### D3. Коды и статусы ошибок

| Случай | Код | Статус | Почему |
|---|---|---|---|
| нет `base_sha` | `DIFF_UNAVAILABLE` (новый) | `409` | состояние прогона не позволяет ответить; не `404` — прогон есть и виден, кабинет сейчас читает `404` как «диффа нет» и спутал бы с `REVIEW_NOT_FOUND` |
| приложение не установлено / приостановлено | `REPOSITORY_NOT_IN_INSTALLATION` (есть) | `404` | смысл кода совпадает дословно; видимость уже пройдена, новых сведений ответ не выдаёт |
| `404` на compare | `COMMIT_NOT_FOUND` (новый) | `410` | установка только что найдена, значит репозиторий есть и доступен — пропал коммит (force-push); `410` отличим от `404 REVIEW_NOT_FOUND` по статусу |
| дифф больше лимита | `DIFF_TOO_LARGE` (новый) | `422` | запрос корректен, ответить на него сервер отказывается по размеру ресурса; `413` — про тело запроса, `502` — провайдер ответил исправно |
| прочий `VcsError` | `SCM_UNAVAILABLE` (есть) | `502` | как у остальных ручек |

- Каждый новый код: значение `ErrorCode`, модель `<Код>Problem(Problem)` с константами и
  примером, вариант в `AnyProblem`, словарь `ERRORS_*`.
- У ручки на `404` и `422` по два кода, а ключ `responses` у статуса один. Как у
  `ERRORS_403_ORIGIN_OR_MANAGE` — словари с объединением моделей по `code`:
  `ERRORS_404_REVIEW_OR_NOT_IN_INSTALLATION` и `ERRORS_422_OR_DIFF_TOO_LARGE`; плюс
  `ERRORS_409_DIFF_UNAVAILABLE`, `ERRORS_410_COMMIT_NOT_FOUND` и `ERRORS_502_SCM_DIFF` (описание:
  видимость, токен пользователя, установка или дифф). `ERRORS_501_NOT_IMPLEMENTED` у ручки
  убирается.
- Доменное «не найдено»: в `app/domain/vcs_errors.py` — `VcsNotFoundError(VcsError)` с кодом
  `PR_NOT_FOUND`; `app/infrastructure/vcs/errors.py` реэкспортирует его вместо своего класса
  (импорты адаптера и тестов не меняются). Тогда use case ловит его, не зная адаптера.
- Исключения use case — в его модуле: `RunDiffUnavailable`, `RepositoryNotInstalled`,
  `RunCommitNotFound`; размер — существующий `DiffTooLargeError` из домена.

Альтернатива для `DIFF_TOO_LARGE` — `409` вместе с `DIFF_UNAVAILABLE`: тоже объединение, но
смешивает «нельзя» и «слишком велико». Для «не установлено» — `502`: неверно, GitHub ответил.

### D4. Кэш диффов — отдельный `InMemoryCacheStore` с лимитом в байтах

- `InMemoryCacheStore(max_entries=…, max_bytes=None, max_entry_bytes=None)`: размер записи —
  длина значения в UTF-8; запись больше `max_entry_bytes` не сохраняется (прежняя запись по
  ключу удаляется); при превышении `max_bytes` или `max_entries` выметаются истёкшие, затем
  самые старые, пока оба лимита не соблюдены. Без новых аргументов поведение прежнее — кэш
  видимости и воркер не меняются.
- Контейнер: поле `_run_diff_cache: CacheStore` и метод `run_diff_cache()`; экземпляр —
  `InMemoryCacheStore(max_bytes=RUN_DIFF_CACHE_MAX_BYTES, max_entry_bytes=RUN_DIFF_CACHE_ENTRY_MAX_BYTES)`.
  Настройки: `RUN_DIFF_CACHE_MAX_BYTES` = 48 МиБ, `RUN_DIFF_CACHE_ENTRY_MAX_BYTES` = 8 МиБ
  (не больше общего — проверка в `Settings`), `RUN_DIFF_CACHE_TTL_SECONDS` = 3600.
- Ключ — `run-diff:v1:{repository.id}:{base_sha}:{head_sha}`: внутренний id репозитория не
  меняется при переименовании и не содержит пользовательских данных. Пользователя в ключе нет:
  видимость прогона проверена до чтения кэша.
- Значение — JSON доменного `DiffFileView` (сериализация — функции модуля use case, как у
  `repo_config_loader`); на попадании модель ответа собирается из доменного представления —
  ручка возвращает `ReviewDiffResponse`, а не `JSONResponse`. Битая запись — промах и
  предупреждение в лог без содержимого.

Альтернативы: общий кэш процесса — диффы вытесняли бы записи видимости (FIFO по числу) и
занимали бы до 1024 × размер диффа; кэш сырого диффа в адаптере — повторный разбор на каждом
попадании и тот же вопрос памяти.

### D5. Use case `load_run_diff`

`app/application/use_cases/run_diff.py`:
`load_run_diff(run, repository, *, installations, vcs, cache, max_bytes, ttl_seconds) -> tuple[DiffFileView, ...]`.
Порядок — от дешёвого к дорогому, и так, чтобы попадание не ходило в сеть:

1. `run.base_sha is None` → `RunDiffUnavailable` (без сети);
2. чтение кэша по ключу D4 → попадание — результат;
3. `installations.repository_installation_id(repository.full_name)` → `None` → `RepositoryNotInstalled`;
4. `vcs.fetch_diff(…, max_bytes=max_bytes)`: `VcsFileTooLargeError` → `DiffTooLargeError`,
   `VcsNotFoundError` → `RunCommitNotFound`, прочий `VcsError` — наружу;
5. `build_diff_view(parse_diff(raw))`, запись в кэш, результат.

Тест use case — без сети: `FakeInstallationCatalog`, `FakeVcs` / `RecordingVcsGateway`,
`InMemoryCacheStore`.

### D6. Свои лимиты VCS для чтений кабинета

`CABINET_VCS_LIMITS = VcsClientLimits(timeout=httpx.Timeout(20.0, connect=5.0), rate_limit_budget_seconds=0.0)`
и ленивый `Container.cabinet_vcs_gateway()` (поле `_cabinet_vcs_gateway`, сборка через
`_build_vcs_gateway`), как `redelivery_vcs_gateway`. Свой клиент и свой `GitHubAppAuth` — свой кэш
installation-токенов. Лимит GitHub не пережидается: поток threadpool не должен спать минуту.
`vcs_gateway()` API и его 5 с не меняются — вебхук в бюджете. Размер диффа на пути API —
своя настройка `RUN_DIFF_MAX_BYTES` (2 МиБ), а не `DIFF_MAX_BYTES` воркера (5 МиБ): разбор,
модель ответа и JSON держат в памяти кратно больше сырого диффа.

Альтернативы: поднять `API_VCS_LIMITS` — ломает бюджет вебхука; общий шлюз воркера в API —
минута ожидания лимита в потоке запроса.

### D7. Проводка API

- `app/api/dependencies.py`: `get_cabinet_vcs_gateway` → `VcsGateway`, `get_run_diff_cache` →
  `CacheStore`; номер установки — через `get_visibility_catalog` (там `CachedInstallationCatalog`
  поверх подменяемого в тестах каталога).
- `app/api/reviews.py`: общая часть `visible_run` выносится в функцию, отдающую прогон и
  репозиторий; новая зависимость `visible_run_with_repository` — для диффа, `visible_run` — как
  был. Правило видимости и порядок «GitHub до чтения прогона» не меняются.
- Ручка остаётся синхронной `def`; ловит исключения use case и отвечает `ApiError` с кодами D3;
  ответ — `ReviewDiffResponse.model_validate({"files": [asdict(f) for f in view]})`.
- Модели ответа не меняются: поля `status` и `kind` остаются `Literal` — схема `200` та же
  (DoD задачи); значения совпадают с `DiffFileStatus`/`DiffLineKind`.
- `_DIFF_DESCRIPTION` — реальное поведение: сессия, видимость, кэш по паре коммитов, коды D3.

## Risks / Trade-offs

- [Переименованный или перенесённый репозиторий: в базе старый `full_name`, GitHub отвечает
  `301`] → `GET …/installation` с JWT разбирается как неверное тело — `502`. Хуже: при номере
  установки из кэша `fetch_diff` получит `301`, а `_request` считает не-ошибку успехом и отдал бы
  тело редиректа как пустой дифф. Задача 4.1: `3xx` в `_request` — `VcsError` (`502`). Обновление
  `full_name` при переименовании — вне этого change.
- [Память контейнера 512 МБ] → лимит размера диффа на пути API (2 МиБ), байтовый лимит кэша
  (48 МиБ) и лимит записи (8 МиБ). Пик одного запроса — сырой дифф, `ParsedFile`, представление,
  модель и JSON одновременно — оценочно 10–20× сырого; несколько параллельных больших диффов
  остаются риском. Числа — оценка, не замер (см. Open Questions).
- [Поток threadpool занят до тайм-аута 20 с, с повторами `5xx` — дольше] → повторов у адаптера
  максимум 3; ожидание лимита выключено.
- [Лимит запросов GitHub App по JWT: `GET /repos/{full_name}/installation` на каждый промах] →
  кэш номера на 5 минут, попадание диффа номер не спрашивает вовсе.
- [Переустановка приложения в пределах TTL: закэширован старый номер, токен на него не выдаётся]
  → `502` до истечения TTL (5 минут). Принято: переустановка редка; сброс кэша на `VcsAuthError`
  не делаем (YAGNI).
- [Кабинет не знает новых кодов] → подзадача фронтенда dmc-268-ui-t1#107: `409`, `410`, `422`
  и `404 REPOSITORY_NOT_IN_INSTALLATION` на ручке диффа — показать замечания списком с причиной.
- [Кэш один на процесс] → при второй реплике у каждой свой кэш: больше запросов к GitHub,
  ответы верные.

## Migration Plan

Миграций нет. Новые настройки со значениями по умолчанию, `infra/` не меняется. Выкат — обычный
деплой; откат — откат коммита: кэш в памяти, состояния не остаётся. Кабинет уже обрабатывает
`501` и после выката сам перейдёт на дифф.

## Open Questions

- Значения по умолчанию `RUN_DIFF_MAX_BYTES` (2 МиБ), `RUN_DIFF_CACHE_MAX_BYTES` (48 МиБ) и
  `RUN_DIFF_CACHE_ENTRY_MAX_BYTES` (8 МиБ) — оценка; уточнить замером пикового RSS на диффе
  в лимит на стенде. Меняются настройкой, без правки спеки и задач.
