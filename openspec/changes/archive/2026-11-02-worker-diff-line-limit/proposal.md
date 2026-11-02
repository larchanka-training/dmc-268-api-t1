## Why

Воркер ограничивает дифф прогона только по байтам (`DIFF_MAX_BYTES`, 5 МиБ), а память разбора
растёт с числом строк, а не с байтами. На ревью PR #308 это показали для ручки диффа в кабинете
(https://github.com/larchanka-training/dmc-268-api-t1/pull/308#discussion_r4432303833); там же
подтвердили, что GitHub отдаёт compare на десятки мегабайт с `200`. У воркера путь тот же —
`parse_diff` на сыром диффе: 5 МиБ строк по одному символу — около 2,6 млн строк, и замер для
задачи #318 даёт пик RSS около 640 МиБ при контейнере воркера 512 МБ. Такой PR кладёт воркер
по OOM, задача уходит на повтор и кладёт его снова.

## What Changes

- `fetch_review_diff` отвергает дифф больше 50 000 строк (константа `REVIEW_DIFF_MAX_LINES`)
  после получения и до разбора: `DiffTooLargeError`. Дальше путь уже есть: прогон — `failed` с
  причиной «дифф больше лимита», шаг `fetching_diff` — `failed` с кодом `DIFF_TOO_LARGE`, модель
  не вызывается, на PR ничего не публикуется.
- Ровно 50 000 строк проходят. Значение то же, что у кабинетного `RUN_DIFF_MAX_LINES`, но пределы
  независимы: у процессов разная память и разные байтовые лимиты.
- Документация называет предел строк рядом с `DIFF_MAX_BYTES`.

## Capabilities

### New Capabilities

### Modified Capabilities

- `webhook-intake`: требование «Лимит размера диффа в воркере» — предел 50 000 строк, проверка
  до разбора; два новых сценария.

## Impact

- Код: `app/application/use_cases/build_review_context.py` (константа и проверка строк).
- Тесты: `tests/application/test_build_review_context.py` (граница),
  `tests/application/test_review_pipeline.py` (прогон `failed`), помощник `diff_of_lines` — в
  `tests/fakes/webhooks.py`.
- Без миграций, без новых настроек, контракт API не меняется.
- Документация: `docs/BACKEND_ARCHITECTURE.md` («Исчерпание ресурсов»), комментарий над
  `DIFF_MAX_BYTES` в `.env.example`.
