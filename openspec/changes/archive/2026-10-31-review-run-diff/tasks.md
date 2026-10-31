Каждая задача — красно-зелёный виток (скилл `backend-tdd`): сначала тест в названном файле
падает, потом код, потом тест зелёный. Решения — `design.md` (D1–D7), поведение — дельта
`specs/review-rest-api/spec.md`.

## 1. Домен: представление диффа

- [x] 1.1 `ParsedFile.is_added: bool = False` (`app/domain/entities.py`), `_parse_file` ставит его по `new file mode` и `--- /dev/null` (`app/domain/diff_parser.py`). Тест: `tests/domain/test_diff_parser.py` — новый текстовый файл, новый бинарный файл, изменённый файл (`False`); прежние тесты парсера и сборки контекста зелёные
- [x] 1.2 Чистая функция `build_diff_view` и типы `DiffLine`, `DiffHunkView`, `DiffFileView` (`app/domain/diff_view.py`), перечисления `DiffLineKind`, `DiffFileStatus` (`app/domain/enums.py`): строки ханка из `Hunk.text` с `kind`, `old_line`, `new_line`, `content` без префикса, `header` — текст после второго `@@`, служебная `\ No newline at end of file` пропускается и номера не сдвигает. Тест: `tests/domain/test_diff_view.py` на литеральных диффах
- [x] 1.3 Там же — уровень файла: статус `added`/`modified`/`deleted`/`renamed`, `previous_path` только у переименования, бинарный — без ханков и с нулевыми счётчиками, `additions`/`deletions`, `language` через `language_for_path` (`.py` → `python`, `.md` → `None`), lock-файл не отфильтрован, пустой дифф — пустой кортеж. Тест: `tests/domain/test_diff_view.py`
- [x] 1.4 Доменный `VcsNotFoundError(VcsError)` с кодом `PR_NOT_FOUND` в `app/domain/vcs_errors.py`; `app/infrastructure/vcs/errors.py` реэкспортирует его вместо своего класса. Тест: `tests/infrastructure/test_github_vcs_gateway.py` — `404` на compare бросает экземпляр доменного класса; `uv run lint-imports` зелёный

## 2. Номер установки приложения

- [x] 2.1 Метод порта `repository_installation_id(full_name) -> int | None` (`app/application/ports/installation_catalog.py`) и адаптер `GitHubInstallationCatalog.repository_installation_id` (`GET /repos/{full_name}/installation` с JWT); `app_installed` — через него. Тест: `tests/infrastructure/test_github_installations.py` — `200` → id, `404` и `suspended_at` → `None`, `5xx`/лимит → `VcsUnavailableError`, `401` → `VcsAuthError`, в заголовке JWT, а не токен пользователя; тесты `app_installed` зелёные
- [x] 2.2 `FakeInstallationCatalog` (`tests/fakes/installation_catalog.py`): `installation_ids` (по умолчанию — `1` для установленного `full_name`), `None` для `not_installed`, `error`, запись в `calls`; новых модулей с фейками нет. Проверка: `uv run mypy .` — все реализации совпадают с портом
- [x] 2.3 Настройка `installation_id_cache_ttl_seconds` (`INSTALLATION_ID_CACHE_TTL_SECONDS`, 300, `gt=0`) в `app/config.py`, строка в `.env.example`. Тест: `tests/test_config.py` — поле в списке положительных и умолчание
- [x] 2.4 `CachedInstallationCatalog.repository_installation_id` (`app/infrastructure/vcs/cached_installations.py`): ключ `installation-id:v1:{full_name}`, TTL из 2.3; `None`, сбой GitHub и сбой хранилища не кэшируются; `app_installed` мимо кэша; докстринг модуля — что кэшируется теперь. Тест: `tests/infrastructure/test_cached_installations.py` — попадание без второго вызова, `None` и `VcsError` спрашиваются снова, битая запись и падающий `CacheStore` — промах, `app_installed` вызывает каталог каждый раз
- [x] 2.5 `Container.visibility_catalog` передаёт TTL номера установки. Тест: `tests/test_container.py`. Документация: строка `InstallationCatalog` в `docs/BACKEND_ARCHITECTURE.md` — `repository_installation_id` и его кэш; «`installation_id` не хранится» остаётся верным

## 3. Кэш диффов с лимитом в байтах

- [x] 3.1 `InMemoryCacheStore(max_bytes=None, max_entry_bytes=None)` (`app/infrastructure/cache/memory.py`): размер — UTF-8 длина значения; запись больше `max_entry_bytes` не сохраняется и удаляет прежнюю по ключу; при превышении — сначала истёкшие, потом самые старые, пока соблюдены и `max_bytes`, и `max_entries`; перезапись пересчитывает размер. Тест: `tests/infrastructure/test_memory_cache.py`; без новых аргументов прежние тесты зелёные
- [x] 3.2 Настройки `run_diff_max_bytes` (2 МиБ), `run_diff_cache_max_bytes` (48 МиБ), `run_diff_cache_entry_max_bytes` (8 МиБ, не больше общего — ошибка `Settings`), `run_diff_cache_ttl_seconds` (3600) в `app/config.py`, строки в `.env.example`. Тест: `tests/test_config.py` — положительность, умолчания, запись больше общего лимита отвергается
- [x] 3.3 Поле `_run_diff_cache` и метод `Container.run_diff_cache()` — свой `InMemoryCacheStore` с лимитами 3.2, не `cache_store()`; поле добавлено и в `tests/e2e/stack.py`. Тест: `tests/test_container.py` — экземпляр один на контейнер, отличается от `cache_store()`, лимиты из настроек

## 4. VCS для кабинета и use case диффа прогона

- [x] 4.1 `GitHubVcsGateway._request`: ответ `3xx` — `VcsError` (переименованный репозиторий не отдаёт тело редиректа как дифф). Тест: `tests/infrastructure/test_github_vcs_gateway.py` — `301` на compare и на `fetch_pr_metadata` → `VcsError`; прочие тесты адаптера зелёные
- [x] 4.2 `CABINET_VCS_LIMITS` (тайм-аут 20 с, подключение 5 с, лимит не пережидается) и ленивый `Container.cabinet_vcs_gateway()` (поле `_cabinet_vcs_gateway`, также в `tests/e2e/stack.py`). Тест: `tests/test_container.py` — тот же экземпляр на повторный вызов, не `vcs_gateway()`, лимиты как заданы; `vcs_gateway()` API — по-прежнему 5 с. Документация: тайм-ауты GitHub в `docs/SYSTEM_DESIGN.md` (Rate Limits & Fallbacks) и строка `VcsGateway` в `docs/BACKEND_ARCHITECTURE.md` («дифф только в воркере» → и в кабинете, свои лимиты)
- [x] 4.3 Use case `load_run_diff` и исключения `RunDiffUnavailable`, `RepositoryNotInstalled`, `RunCommitNotFound` (`app/application/use_cases/run_diff.py`): прогон без `base_sha` — `RunDiffUnavailable` без обращений к каталогу, шлюзу и кэшу. Тест: `tests/application/test_run_diff.py`
- [x] 4.4 Там же — промах и попадание: промах спрашивает номер установки, зовёт `fetch_diff(full_name, base_sha, head_sha, installation_id, max_bytes=…)`, строит представление, пишет JSON в кэш по `run-diff:v1:{repository.id}:{base_sha}:{head_sha}`; попадание не трогает ни каталог, ни шлюз и даёт равное представление. Тест: `tests/application/test_run_diff.py` с `FakeInstallationCatalog`, `FakeVcs`, `InMemoryCacheStore`
- [x] 4.5 Там же — отказы: номер `None` → `RepositoryNotInstalled` без `fetch_diff`; `VcsFileTooLargeError` → `DiffTooLargeError`; `VcsNotFoundError` → `RunCommitNotFound`; прочий `VcsError` — наружу; после отказа кэш пуст; битая запись — промах; запись больше лимита записи не сохраняется, результат отдан. Тест: `tests/application/test_run_diff.py`
- [x] 4.6 Документация кэша: `docs/SYSTEM_DESIGN.md` §4.3 («Кэш файлов (запланировано)» — дифф прогона реализован: отдельное хранилище API, ключ, лимиты) и строка `CacheStore` в `docs/BACKEND_ARCHITECTURE.md` (третий потребитель — дифф прогона, своё хранилище). Проверка: `uv run pytest tests/test_docs.py`

## 5. HTTP API

- [x] 5.1 Коды `DIFF_UNAVAILABLE` (409), `COMMIT_NOT_FOUND` (410), `DIFF_TOO_LARGE` (422): значения `ErrorCode`, модели `DiffUnavailableProblem`, `CommitNotFoundProblem`, `DiffTooLargeProblem` с константами и примером, варианты `AnyProblem` (`app/api/problems.py`); словари `ERRORS_404_REVIEW_OR_NOT_IN_INSTALLATION`, `ERRORS_409_DIFF_UNAVAILABLE`, `ERRORS_410_COMMIT_NOT_FOUND`, `ERRORS_422_OR_DIFF_TOO_LARGE`, `ERRORS_502_SCM_DIFF` (`app/api/errors.py`). Тест: `tests/test_docs.py` (каталог, `oneOf` с discriminator по `code`)
- [x] 5.2 Зависимости `get_cabinet_vcs_gateway` и `get_run_diff_cache` (`app/api/dependencies.py`); в `app/api/reviews.py` общая часть `visible_run` — функция, отдающая прогон и репозиторий, и зависимость `visible_run_with_repository`. Тест: `tests/test_reviews_api.py` — тесты видимости (`RUN_PATHS`) зелёные без изменений
- [x] 5.3 Ручка `get_review_diff` (синхронная `def`): `load_run_diff` с `get_visibility_catalog`, шлюзом кабинета, кэшем диффов и `run_diff_max_bytes`, ответ — `ReviewDiffResponse` из доменного представления. Тест: `tests/test_reviews_api.py` — `test_diff_of_a_visible_run_answers_not_implemented` заменён тестами `200` по сценариям «Дифф прогона» (изменённый, добавленный и удалённый, переименование, бинарный, `uv.lock`, `\ No newline`) и `401` без сессии; шлюз подменён `FakeVcs` через `get_cabinet_vcs_gateway`
- [x] 5.4 Отказы ручки: `409 DIFF_UNAVAILABLE` без вызовов GitHub, `404 REPOSITORY_NOT_IN_INSTALLATION`, `410 COMMIT_NOT_FOUND`, `422 DIFF_TOO_LARGE`, `502 SCM_UNAVAILABLE` на сбой установки и диффа; чужой прогон — `404 REVIEW_NOT_FOUND` без вызовов шлюза и каталога установки. Тест: `tests/test_reviews_api.py`
- [x] 5.5 Кэш на уровне ручки: два запроса одного прогона — один `fetch_diff` и один запрос установки; второй пользователь с видимым репозиторием получает дифф из кэша; два прогона одного репозитория — один запрос установки; после `502` повтор идёт к GitHub. Тест: `tests/test_reviews_api.py`
- [x] 5.6 Настоящие ответы трёх новых кодов в `tests/test_problem_details.py::test_server_bodies_match_their_models` (множество `seen` совпадает с `ErrorCode` без `REVIEW_ALREADY_ACTIVE`). Проверка: тест зелёный
- [x] 5.7 `_DIFF_DESCRIPTION` — реальное поведение (сессия, видимость, кэш по паре коммитов, коды отказов, ссылка на #87); у роута нет `ERRORS_501_NOT_IMPLEMENTED`, есть словари 5.1. Экспорт: `uv run python scripts/export_openapi.py`, `docs/openapi/openapi.json` — в том же коммите; форма `ReviewDiffResponse` в экспорте не изменилась. Проверка: `uv run python scripts/export_openapi.py --check`, `uv run pytest tests/test_docs.py tests/api/test_contract_guards.py`

## 6. Синхронизация и проверки

- [x] 6.1 Сверка дельты с кодом: каждый сценарий `specs/review-rest-api/spec.md` покрыт тестом из групп 1–5; расхождение правится в дельте или в коде, не молча. Проверка: `npx @fission-ai/openspec@1.15.0 validate review-run-diff --strict`
- [x] 6.2 Статические проверки: `uv lock --check`, `uv run ruff check .`, `uv run lint-imports`, `uv run mypy .`, `uv run python scripts/export_openapi.py --check`, `uv run alembic heads` — одна голова, новых ревизий нет
- [x] 6.3 Полный прогон как в CI: `PYTEST_FAIL_ON_SKIP=1 uv run pytest` с `TEST_DATABASE_URL`, `TEST_RABBITMQ_URL`, `TEST_RABBITMQ_CONTAINER` — без пропусков
- [x] 6.4 `npx @fission-ai/openspec@1.15.0 validate --all --strict` зелёный

## 7. Архив change

- Когда закрыты все задачи 1–6 — `npx @fission-ai/openspec@1.15.0 archive review-run-diff` в том же PR, до мерджа (CI падает на незаархивированном change с закрытыми задачами).
- После архива: в `openspec/specs/review-rest-api/spec.md` есть «Дифф прогона», «Отказы диффа прогона», «Дифф прогона кэшируется по паре коммитов», «Установка приложения на репозиторий кэшируется», нет «Дифф прогона описан заглушкой»; `validate --all --strict` и `list --json | python3 scripts/check_openspec_archived.py` зелёные.
