## Why

Фронтенду на странице прогона ревью нужны заголовок и ссылка на запрос на изменение, число отсеянных находок и опубликованные комментарии. Всё это уже лежит в базе, но `GET /api/v1/reviews/{review_run_id}` отдавал только `merge_request_id`, и за остальным пришлось бы ходить отдельными запросами, которых нет.

## What Changes

- В ответ прогона добавлены `rejected_findings`, сводка `merge_request` (номер, заголовок, автор, ветки, состояние, `url`, репозиторий) и `published_comments` — по времени публикации, затем по id. `merge_request_id` остаётся.
- `url` строит доменная функция `merge_request_url`: для GitHub — страница pull request, для остальных провайдеров — `null`.
- `PublishedCommentRepo.list_for_run` отдаёт комментарии в стабильном порядке.

## Impact

- Код: `app/api/reviews.py`, `app/api/schemas.py` (общая база `MergeRequestBase` для `MergeRequestResponse` и сводки), `app/api/pull_requests.py`, `app/domain/provider_links.py`, `app/infrastructure/db/repositories.py`.
- Контракт: `docs/openapi/openapi.json` — новые поля только добавлены, существующие не меняются.
- Тесты: `tests/test_reviews_api.py`, `tests/domain/test_provider_links.py`, `tests/db/test_adapters.py`.
