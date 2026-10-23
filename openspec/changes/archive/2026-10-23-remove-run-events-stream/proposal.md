## Why

Экран прогона во фронтенде перешёл на общий поток `GET /api/v1/events` (ui#52 смёржен и выкачен) и отбирает события своего прогона по id. У потока одного прогона `GET /api/v1/reviews/{review_run_id}/events` клиентов не осталось, а два потока — два цикла опроса, две модели событий и два набора тестов на одно поведение. Задача #103.

## What Changes

- **BREAKING**: `GET /api/v1/reviews/{review_run_id}/events` удалён; путь отвечает `404` `ROUTE_NOT_FOUND`, как любой неизвестный. Клиентов у него нет.
- Из контракта уходят путь и компонент `ReviewRunStatusEvent`.
- Удалены роутер потока, цикл опроса одного прогона, решение «что отправить» (`next_event`) и чтение прогона для потока. Интервалы опроса и проверки сессии переехали в цикл общего потока; `ping` остался общим.
- Спека `review-run-events` удаляется целиком. Требования, которые ссылались на старый путь (сессия, Problem Details, trace id, UTC, трассировка SSE, закрытие потока при выходе), теперь говорят об общем потоке.
- Тесты старого потока удалены; проверки, важные и для общего потока (событие уходит до конца запроса, `X-Trace-ID` потока равен trace id спана, выход в другой вкладке закрывает поток, сессия перепроверяется по часам при медленном чтении), перенесены на `GET /api/v1/events`.

## Capabilities

### Removed Capabilities

- `review-run-events`: поток событий статуса одного прогона.

### Modified Capabilities

- `review-rest-api`: «Ручки чтения требуют сессию», «Ошибки отдаются в формате Problem Details», «Каждый ответ несёт trace id», «Время в ответах — UTC с Z».
- `user-auth`: «Поток событий закрывается вместе с сессией».
- `observability`: «Трассировка не копит поток SSE».

## Impact

- Код: удалены `app/api/review_events.py`, `app/application/review_run_events.py`, `get_run_reader` в `app/api/dependencies.py`; `app/domain/run_events.py` сокращён до `PingEvent` и `PING_AFTER_SECONDS`; `app/api/factory.py`, `app/application/run_feed_events.py`, описание `GET /api/v1/events`.
- Контракт: `docs/openapi/openapi.json` без пути и `ReviewRunStatusEvent`.
- Документы: `docs/SYSTEM_DESIGN.md`, `docs/pipeline/PIPELINE_SPEC.md`, `docs/pipeline/TEAM_ALIGNMENT_CHECKLIST.md`.
- Параллельно открыт PR #111, он тоже правит `app/api/review_events.py`; конфликт решается при мёрже второго удалением файла.
- OpenSpec: change `add-review-run-events`, который ввёл поток, так и не был заархивирован (ждал проверки на стенде, задача 8.3). Он архивируется в том же PR перед этим change: его этап сбоя (`failed_stage`) и поля ответа попадают в спеки, а спека `review-run-events` создаётся и тут же удаляется этим change. Этап сбоя вынесен в отдельное требование «Сбой фиксирует этап», чтобы текст «Жизненного цикла прогона ревью» остался короче 500 символов.
