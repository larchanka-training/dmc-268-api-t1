## Why

Независимое ревью PR #105 (задача #103) показало: по событиям общего потока фронтенд не может надёжно поддерживать строку списка. Не определено, что приходит при итоговом статусе; у `run.status_changed` и `run.finished` нет `updated_at`, у `run.finished` — ещё и `failed_stage`; событие нечем упорядочить относительно снимка `GET /reviews`. Кроме того, вход `created_from` / `created_to` требовал в схеме суффикс `Z`, хотя сервер принимает любую зону, а правило пустой строки было задано только для `q`.

## What Changes

- Данные всех событий прогона в `GET /api/v1/reviews/events` — `ReviewRunListItem`, полная строка списка. Тип события — подсказка; клиент делает upsert по `id`, побеждает больший `updated_at`; при переходе в итоговый статус приходит только `run.finished`; клиент сначала подписывается на поток, затем запрашивает снимок. Модели `ReviewRunStatusChangedEvent` и `ReviewRunFinishedEvent` удалены, связь события с моделью — `x-sse-events` в ответе потока.
- `created_from` / `created_to` — время с любой зоной, в схеме без `pattern` ответа.
- У `q` и `author` пустая строка — без фильтра.
- Обратный диапазон дат проверяет модель параметров запроса: ошибка приходит вместе с остальными ошибками схемы.
- В описании списка: сортировка по `id` по убыванию при равном `created_at`; `findings_count` — то же, что число `findings` в `GET /reviews/{review_run_id}`; невидимый `repository_id` — пустой список.

## Impact

- Код: `app/api/reviews.py`, `app/api/schemas.py` (`UtcDatetimeParam`), `app/api/errors.py`, `app/api/factory.py`.
- Контракт: `docs/openapi/openapi.json` — две схемы событий удалены, у потока `x-sse-events`, у параметров дат нет `pattern`. Фронтенд синхронизирует типы заново.
- Тесты: `tests/test_problem_details.py`, `tests/test_reviews_api.py`, `tests/api/test_contract_guards.py`.
