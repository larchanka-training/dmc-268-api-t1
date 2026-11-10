## Why

Задача #388 (родитель #381). Change `context-payload-retention` (#382) научил воркер стирать
`context_payloads.body` через `CONTEXT_PAYLOAD_RETENTION_DAYS` (30 дней) и ставить
`body_expired_at`. API об этом молчит: ни одна ручка не читает `body_expired_at`, а
`GET /api/v1/reviews/{review_run_id}/diff` берёт дифф у GitHub и после срока хранения продолжает
его отдавать. Интерфейс (ui#140) должен показать «дифф удалён по сроку хранения» и отличать это
от «изменений нет».

## What Changes

- `GET /api/v1/reviews/{review_run_id}` отдаёт `diff_expired_at`: время, когда дифф прогона
  удалён по сроку хранения, — `max(body_expired_at)` по пакетам контекста прогона, если пакеты
  есть и тела **всех** стёрты; иначе `null`. У прогона без пакетов — `null`.
- `GET /api/v1/reviews/{review_run_id}/diff` у такого прогона отвечает `410 DIFF_EXPIRED`
  (новый код в каталоге ошибок, модель `DiffExpiredProblem`) — после проверки видимости, раньше
  проверки `base_sha`, кэша и любых запросов к GitHub. У прогона без пакетов контекста дифф
  отдаётся, как раньше.
- «Изменений нет» по-прежнему `200` с `files: []`, а не отказ.
- Время удаления машиночитаемо отдаёт поле прогона; `ApiError` расширениями не дополняется,
  в ответе `410` время есть только в `detail`.

## Capabilities

### New Capabilities

### Modified Capabilities

- `review-rest-api`: поле `diff_expired_at` в результате прогона; отказ `410 DIFF_EXPIRED` у
  ручки диффа.

## Impact

- `app/domain/payload_retention.py` (чистая функция `diff_expired_at`),
  `app/application/ports/repositories.py`, `app/infrastructure/db/repositories.py`
  (`ContextPayloadRepo.body_expiry_stats_for_run`), `tests/fakes/unit_of_work.py`,
  `app/application/use_cases/run_diff.py` (`RunDiffExpired`).
- `app/api/reviews.py`, `app/api/problems.py`, `app/api/errors.py`,
  `docs/openapi/openapi.json`.
- `docs/erd.md`, `docs/BACKEND_ARCHITECTURE.md`: кто читает `body_expired_at`.
- Миграция не нужна: `body_expired_at` и индекс уже есть (`0019`).
- Фронт (ui#140) — потребитель контракта: `diff_expired_at` и `410 DIFF_EXPIRED`.
