## Why

Ручка `GET /api/v1/reviews/{review_run_id}/diff` (change `review-run-diff`, PR #308) ограничивает
дифф только по байтам (`RUN_DIFF_MAX_BYTES`, 2 МиБ). На ревью AndrewKonst показал, что байтовый
лимит не ограничивает число строк: 2 МиБ строк по одному символу — около миллиона строк, и разбор
с построением представления даёт пик около 800 МиБ (`tracemalloc`) при контейнере API в 512 МБ.
Такой дифф кладёт процесс при каждом открытии экрана прогона.

Там же был вопрос, что compare GitHub с `application/vnd.github.diff` отвечает на дифф больше
своего предела. Проверка запуском показала `422` «The diff is taking too long to generate.».
Адаптер сейчас считает его прочим сбоем, и пользователь видит `502 SCM_UNAVAILABLE`, хотя
причина та же, что у `DIFF_TOO_LARGE`.

## What Changes

- Use case `load_run_diff` отвергает дифф больше 50 000 строк (константа `RUN_DIFF_MAX_LINES`)
  до разбора: `422 DIFF_TOO_LARGE`, сырой дифф в кэш не кладётся.
- Адаптер GitHub переводит `422` на compare в `VcsFileTooLargeError`. Use case уже превращает его
  в `DiffTooLargeError`, поэтому ручка отвечает `422 DIFF_TOO_LARGE`, а не `502`, а воркер
  переводит прогон в `failed` с `DIFF_TOO_LARGE`, без новой логики воркера.
- Текст отказа `DIFF_TOO_LARGE` и описания ручки и модели ошибки называют все три причины:
  байты, строки, отказ GitHub. Экспорт `docs/openapi/openapi.json` обновляется.
- Оценка памяти кэша диффов исправлена: 24 записи по 2 МиБ занимают ≈ 48 МиБ только для ASCII,
  с кириллицей ≈ 96 МиБ, до ≈ 192 МиБ с символом вне BMP (PEP 393). Число записей не меняется.

## Capabilities

### New Capabilities

### Modified Capabilities

- `review-rest-api`: требование «Отказы диффа прогона» — `422 DIFF_TOO_LARGE` также на дифф
  больше 50 000 строк и на отказ GitHub строить дифф; два новых сценария.
- `webhook-intake`: требование «Лимит размера диффа в воркере» — сценарий отказа GitHub
  (`422` compare) → прогон `failed` с `DIFF_TOO_LARGE`.

## Impact

- Код: `app/application/use_cases/run_diff.py` (константа и проверка строк),
  `app/infrastructure/vcs/github.py` (`422` compare → `VcsFileTooLargeError`), `app/api/reviews.py`,
  `app/api/problems.py`, `app/api/errors.py` (тексты), комментарий в
  `app/infrastructure/container.py`.
- Контракт API: коды и форма ответов не меняются, меняются описания; экспорт
  `docs/openapi/openapi.json`.
- Без миграций, без новых настроек, без изменений инфраструктуры.
- Документация: `docs/SYSTEM_DESIGN.md` §4.3, строка `CacheStore` в
  `docs/BACKEND_ARCHITECTURE.md`.
- Лимит строк в воркере — вне этого change, отдельная задача.
