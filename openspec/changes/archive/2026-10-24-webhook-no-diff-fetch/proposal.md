## Why

Приём вебхука синхронно качает весь compare-дифф PR (`handle_webhook.py`) ради раннего 502 и выбрасывает результат: воркер всё равно качает дифф сам. Лимита размера нет ни у приёма, ни у воркера — большой PR занимает память API и съедает 10 секунд, за которые GitHub ждёт ответа на доставку. Комментарии и `description` ручки вебхука обещают, что «хостинг повторит доставку» на не-2xx, — это неверно: GitHub неудачные доставки сам не повторяет («GitHub does not automatically redeliver failed deliveries»), их переотправляют вручную или будущая задача #149. Задача #144.

## What Changes

- Приём вебхука достаёт через `VcsGateway` только метаданные PR (нужен `base_sha`); сбой VCS на них — по-прежнему 502 до создания записей. Дифф приём не запрашивает.
- Дифф достаёт только воркер, с лимитом размера `DIFF_MAX_BYTES` (по умолчанию 5 МиБ): адаптер читает тело потоком и не дочитывает его дальше лимита. `fetch_diff` порта принимает обязательный `max_bytes`.
- Дифф прогона больше лимита — прогон `failed` с причиной «дифф больше лимита», шаг `fetching_diff` — `failed` с новым кодом `DIFF_TOO_LARGE`; модель не вызывается, частичного ревью нет, сообщение уходит в DLQ, повторная доставка задачи прогон не перезапускает.
- Инкрементальный дифф для закрытия тредов (прошлый head → текущий) — с тем же лимитом; превышение не роняет прогон, изменение строки якоря считается неподтверждённым.
- Комментарии кода, `description` ручки `POST /api/v1/webhooks/{provider}` и требование «Сбой постановки задачи переводит прогон в failed» больше не обещают повтор доставки хостингом.

## Capabilities

### Modified Capabilities

- `webhook-intake`: новые требования «Лимит размера диффа в воркере» и «Инкрементальный дифф тредов ограничен тем же лимитом»; «Получение диффа через VCS-шлюз» — приём берёт только метаданные, дифф берёт воркер; «Приём не разбирает и не хранит дифф» — приём дифф не качает; «Воркер разбирает дифф на файлы и hunk'и» — воркер достаёт дифф впервые, а не повторно; «Сбой постановки задачи переводит прогон в failed» — 5xx не означает, что GitHub повторит доставку.

## Impact

- Код: `app/application/use_cases/handle_webhook.py`, `app/application/use_cases/build_review_context.py` (`fetch_review_diff`), `app/application/publish_review.py`, `app/application/review_pipeline.py`, порт `VcsGateway.fetch_diff`, адаптер `GitHubVcsGateway.fetch_diff`, `DiffTooLargeError` в `app/domain/vcs_errors.py`, настройка `diff_max_bytes` и `ContextLimits.diff_max_bytes`.
- Контракт API: в `ReviewErrorCode` добавляется `DIFF_TOO_LARGE`; меняется `description` ручки вебхука — `docs/openapi/openapi.json` перегенерируется.
- Документация: `docs/pipeline/PIPELINE_SPEC.md` (§6 и каталог кодов §8), `docs/BACKEND_ARCHITECTURE.md`, `README.md`, `.env.example`.
