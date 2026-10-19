> Заменён change `openapi-from-code` до мёржа: PR #69 закрыт без мёржа, `openapi.yaml` и
> сверка с ним в develop не попали. Архив оставлен как история решения.

## Why

`docs/openapi/openapi.yaml` разошёлся с роутами FastAPI. Ручек
`GET /api/v1/repositories` и `GET /api/v1/repositories/{repository_id}/pull-requests`
в контракте нет, хотя они реализованы и требуются спекой `review-rest-api`. Зато в
контракте есть `GET /reviews` и `GET /reviews/{reviewId}/findings`, которых нет ни в
коде, ни в спеках: change `add-review-rest-api` отдаёт находки в ответе прогона, а не
отдельной ручкой. `GET /reviews/{reviewId}` описан схемой `ReviewJob` в `camelCase`,
а сервер отдаёт прогон с находками в `snake_case`; 404 описан как `ApiError`, а
сервер отвечает телом FastAPI `{"detail": …}`. Фронтенд генерирует клиент по
контракту, поэтому неверный контракт — это неверный клиент.

## What Changes

- Описать `GET /repositories` и `GET /repositories/{repositoryId}/pull-requests` по
  коду: схемы ответа, `limit`/`offset` с потолком 200, `401`, `404`, `422`.
- Описать ответ `GET /reviews/{reviewId}` схемами `ReviewRun` и `ReviewRunFinding`
  по pydantic-моделям `app/api/reviews.py`; `404` и `422` — телами FastAPI.
- У потока `/reviews/{reviewId}/events` — те же `404` и `422`.
- Удалить `GET /reviews` и `GET /reviews/{reviewId}/findings`: их не требует ни одна
  спека, это следы плана до `add-review-rest-api`.
- `POST /reviews` оставить с пометкой «не реализовано»: это план ручного запуска
  (`PIPELINE_SPEC.md` §4), а требования и задачи на него пока нет.
- Вебхук: ответ `202` с `{"status": "acknowledged"}` на `pull_request_review_thread`,
  `422` на провайдера вне `Provider`.
- Убрать `REVIEW_NOT_FOUND` из `ApiError.code`: ни один ответ сервера его не отдаёт.
- Синхронизировать список ручек и кодов ошибок в `docs/pipeline/PIPELINE_SPEC.md` §9.

Код и спеки не меняются.

## Capabilities

### New Capabilities

Нет.

### Modified Capabilities

Нет: поведение ручек не меняется, правится только их описание.

## Impact

- `docs/openapi/openapi.yaml`, `docs/pipeline/PIPELINE_SPEC.md`.
- Фронтенд: сгенерированный по контракту клиент увидит ручки репозиториев и запросов на
  изменение и настоящую форму ответа прогона.
