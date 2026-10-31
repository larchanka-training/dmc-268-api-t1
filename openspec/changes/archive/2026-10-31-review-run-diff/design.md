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
  ни то требование, ни `CachedInstallationCatalog` не трогает: номер установки он не кэширует
  (D2); порядок архивации двух change'ей не важен.

## Goals / Non-Goals

**Goals:**

- Реальная ручка диффа без изменения формы ответа `200` и без миграций.
- Решение о форме диффа — чистая функция домена; ввод-вывод — в use case и адаптерах.
- Память API ограничена: и на один запрос (лимит размера диффа), и на кэш (число записей, каждая
  не больше лимита диффа).

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

### D2. Номер установки — метод `InstallationCatalog`, без кэша

- Порт: `repository_installation_id(full_name: str) -> int | None` — `None`, если приложение не
  установлено (`404`) или установка приостановлена.
- `GitHubInstallationCatalog`: тот же `GET /repos/{full_name}/installation` с JWT, что у
  `app_installed`; `app_installed` переписывается через новый метод (`is not None`) — один запрос,
  одна ветка разбора.
- Номер не кэшируется. Ручка берёт некэширующий каталог (`get_installation_catalog`), а use case
  спрашивает номер только на промахе кэша диффов, рядом с запросом диффа: попадание не спрашивает
  ни номер, ни дифф. Промах и так идёт в сеть за диффом, второй короткий запрос с JWT к нему
  ничего заметного не добавляет.
- `CachedInstallationCatalog` и его докстринг («кэшируется только `list_repositories`») не
  меняются.
- Двойники: `FakeInstallationCatalog` получает `installation_ids: dict[str, int] | None`
  (по умолчанию — `1` для любого установленного `full_name`) и пишет вызов в `calls`; новых
  модулей с фейками не заводим.

Почему без кэша (на ревью кэш был, его убрали): закэшированный номер переживает переустановку
приложения — installation-токен на старый номер не выдаётся, и ручка отвечала `502` до конца TTL
(5 минут); при приостановленной установке с номером из кэша GitHub отвечает `403`, и вместо
`404 REPOSITORY_NOT_IN_INSTALLATION` из спеки получался `502`. Сброс кэша по отказу — лишняя
ветка ради экономии одного запроса на промахе.

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

### D4. Кэш диффов — отдельный `InMemoryCacheStore`, в нём сырой дифф

- Контейнер: поле `_run_diff_cache: CacheStore` и метод `run_diff_cache()`; экземпляр —
  `InMemoryCacheStore(max_entries=24)`, свой, не `cache_store()`: диффы не вытесняют записи
  видимости (FIFO по числу). Запись не больше `RUN_DIFF_MAX_BYTES` (2 МиБ) — больший дифф
  отвергается до кэша (`DIFF_TOO_LARGE`), поэтому кэш держит не больше 24 × 2 МиБ ≈ 48 МиБ.
  Учёта байтов в `InMemoryCacheStore` нет: хватает ограничения по числу записей.
- TTL — константа `_CACHE_TTL_SECONDS = 3600` в модуле use case, не настройка: дифф пары
  коммитов не меняется, срок ограничивает только память, и подбирать его на стенде незачем.
- Ключ — `run-diff:v1:{repository.id}:{base_sha}:{head_sha}`: внутренний id репозитория не
  меняется при переименовании и не содержит пользовательских данных. Пользователя в ключе нет:
  видимость прогона проверена до чтения кэша.
- Значение — сырой текст диффа, как его отдал GitHub; на попадании — `parse_diff` и
  `build_diff_view` заново, модель ответа собирает ручка из доменного представления
  (`ReviewDiffResponse`, а не `JSONResponse`). Сбой хранилища — промах и предупреждение в лог без
  содержимого.

Почему сырой дифф, а не JSON представления: замер на диффе 1,9 МиБ — повторный разбор 76 мс,
разбор JSON представления 87 мс, а сам JSON — 5,3 МиБ, почти втрое больше сырого. Сериализация
представления — лишний код без выигрыша по времени и с проигрышем по памяти.

Альтернатива — общий кэш процесса: диффы вытесняли бы записи видимости и занимали бы до
1024 × размер диффа. Отвергнута.

### D5. Use case `load_run_diff`

`app/application/use_cases/run_diff.py`:
`load_run_diff(run, repository, *, installations, vcs, cache, max_bytes) -> tuple[DiffFileView, ...]`.
Порядок — от дешёвого к дорогому, и так, чтобы попадание не ходило в сеть:

1. `run.base_sha is None` → `RunDiffUnavailable` (без сети);
2. чтение кэша по ключу D4 → попадание — `build_diff_view(parse_diff(raw))` из записи;
3. `installations.repository_installation_id(repository.full_name)` → `None` → `RepositoryNotInstalled`;
4. `vcs.fetch_diff(…, max_bytes=max_bytes)`: `VcsFileTooLargeError` → `DiffTooLargeError`,
   `VcsNotFoundError` → `RunCommitNotFound`, прочий `VcsError` — наружу;
5. запись сырого диффа в кэш на `_CACHE_TTL_SECONDS`, `build_diff_view(parse_diff(raw))`, результат.

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
  `CacheStore`; номер установки — через `get_installation_catalog`, без кэша (D2).
- `app/api/reviews.py`: проверка видимости — одна зависимость `visible_run_with_repository`,
  отдающая прогон и репозиторий; `visible_run` — её подзависимость FastAPI, отдающая только прогон,
  для остальных ручек. Правило видимости и порядок «GitHub до чтения прогона» не меняются.
- Ручка остаётся синхронной `def`; ловит исключения use case и отвечает `ApiError` с кодами D3;
  ответ — `ReviewDiffResponse.model_validate({"files": [asdict(f) for f in view]})`.
- Модели ответа не меняются: поля `status` и `kind` остаются `Literal` — схема `200` та же
  (DoD задачи); значения совпадают с `DiffFileStatus`/`DiffLineKind`.
- `_DIFF_DESCRIPTION` — реальное поведение: сессия, видимость, кэш по паре коммитов, коды D3.

## Risks / Trade-offs

- [Переименованный или перенесённый репозиторий: в базе старый `full_name`, GitHub отвечает
  `301`] → `GET …/installation` с JWT разбирается как неверное тело — `502`. Хуже: `fetch_diff`
  с `301` — `_request` считает не-ошибку успехом и отдал бы тело редиректа как пустой дифф. Задача 4.1: `3xx` в `_request` — `VcsError` (`502`). Обновление
  `full_name` при переименовании — вне этого change.
- [Память контейнера 512 МБ] → лимит размера диффа на пути API (2 МиБ) и 24 записи кэша
  сырого диффа (до 48 МиБ). Пик одного запроса — сырой дифф, `ParsedFile`, представление,
  модель и JSON одновременно — оценочно 10–20× сырого; несколько параллельных больших диффов
  остаются риском. Числа — оценка, не замер (см. Open Questions).
- [Поток threadpool занят до тайм-аута 20 с, с повторами `5xx` — дольше] → повторов у адаптера
  максимум 3; ожидание лимита выключено.
- [Лимит запросов GitHub App по JWT: `GET /repos/{full_name}/installation` на каждый промах] →
  попадание диффа номер не спрашивает вовсе; промах и так тратит запрос на дифф.
- [Попадание отдаёт дифф без обращения к GitHub, даже если приложение уже снято с репозитория]
  → дифф пары коммитов тот же, видимость прогона проверена; не дольше часа (TTL).
- [Кабинет не знает новых кодов] → подзадача фронтенда dmc-268-ui-t1#107: `409`, `410`, `422`
  и `404 REPOSITORY_NOT_IN_INSTALLATION` на ручке диффа — показать замечания списком с причиной.
- [Кэш один на процесс] → при второй реплике у каждой свой кэш: больше запросов к GitHub,
  ответы верные.

## Migration Plan

Миграций нет. Новые настройки со значениями по умолчанию, `infra/` не меняется. Выкат — обычный
деплой; откат — откат коммита: кэш в памяти, состояния не остаётся. Кабинет уже обрабатывает
`501` и после выката сам перейдёт на дифф.

## Open Questions

- Значение по умолчанию `RUN_DIFF_MAX_BYTES` (2 МиБ) и 24 записи кэша — оценка; уточнить
  замером пикового RSS на диффе в лимит на стенде. Лимит меняется настройкой, число записей —
  константой в контейнере, без правки спеки.
