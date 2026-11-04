## Context

D11 change `manual-review-launch` (`openspec/changes/archive/2026-11-03-manual-review-launch/design.md`)
свёл правило «пауза авторевью не перекрывает ручной прогон» в чистую `pause_check(repository,
head_run)`, где `head_run` — последний прогон на head (`find_latest`). Упавший ручной прогон на
паузе по D11 даёт check паузы. D11 не учёл повтор: на одном head может быть завершённый ручной
M1 и после него упавший ручной M2 — тогда последний прогон M2, и gate M1 пропадает.

## Goals / Non-Goals

**Goals:** упавший или отменённый повторный ручной прогон на паузе возвращает gate последнего
завершённого ручного прогона на head; правило — в одном месте.

**Non-Goals:** правило дубля вебхука (D4) и проверка «прогон вытеснен» не меняются; поведение при
включённом авторевью и при выключенном репозитории — прежнее.

## Decisions

### D1. Прогон, отвечающий за head, выбирает `head_run_in_charge`

```
head_run_in_charge(uow, repository, merge_request_id, head_sha) -> ReviewRun | None
  latest = find_latest(merge_request_id, head_sha)
  на паузе авторевью (connected_at задан, auto_review_enabled = false)
  и latest не ручной в работе/завершённый:
      find_completed_manual(merge_request_id, head_sha) or latest
  иначе latest
```

- Зовётся внутри открытого вызывающим `with uow`, как и прежний `find_latest`.
- `pause_check` остаётся чистой и с прежней сигнатурой: получив завершённый M1, она даёт `None`,
  и gate собирается по находкам M1. Альтернатива — передать в `pause_check` оба прогона — тянет
  решение «какой прогон» в функцию, которая решает «пауза или нет».
- Выключенный репозиторий: `find_completed_manual` не нужен — `pause_check` и так даёт
  `DISABLED_REVIEW_CHECK`.
- Вебхуковый прогон до паузы `find_completed_manual` не находит — check паузы, как раньше.

### D2. Где помощник, а где настоящий последний прогон

- `settle_merge_gate`: помощник. M1 завершён — gate по его находкам (ветка «новый, успевший
  завершиться»).
- `publish_pause_check`: согласование после POST по помощнику — M2 упал, M1 завершён →
  `settle_merge_gate(run_id=M1)`.
- Упавший прогон (`_block_head_of_failed_run`): «вытеснен ли» — по `find_latest`; на паузе, если
  помощник вернул завершённый ручной прогон, — `settle_merge_gate(run_id=его id)` вместо check
  паузы.
- Вебхук треда: помощник и в `pause_check`, и в `refresh_merge_gate_for_merge_request` — иначе
  упавший M2 остановил бы пересчёт по закрытию треда M1.
- Вебхук PR: прогон на head для `pause_check` — помощник; правило дубля — `find_latest`.

### D3. `find_completed_manual` — новый метод порта

Последний по `(created_at, id)` прогон `trigger = manual` со статусом `completed` на этот head.
`find_reviewed` не подходит: он только про вебхуковые прогоны (D4 `manual-review-launch`).

## Risks / Trade-offs

- [Лишний запрос на паузе] → только когда последний прогон не ручной в работе; прогонов на PR
  единицы.
