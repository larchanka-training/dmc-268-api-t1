## Context

D1 change `manual-gate-after-failed-relaunch`
(`openspec/changes/archive/2026-11-04-manual-gate-after-failed-relaunch/design.md`) ввёл
`head_run_in_charge`: на паузе авторевью, если последний прогон на head — не ручной в работе или
завершённый, за head отвечает последний завершённый ручной прогон. Условие «не ручной в работе
или завершённый» покрывает и вебхуковый прогон. Тогда вебхуковый прогон A после завершённого
ручного M1 на том же head (retarget PR без нового коммита) на паузе получал gate M1 вместо check
паузы — вопреки сценарию «Прогон завершился после включения паузы». Ревью PR #335, Grinv.

## Goals / Non-Goals

**Goals:** подставлять завершённый ручной прогон только вместо упавшего или отменённого ручного;
убрать из мест вызова ветвление на отсутствующий репозиторий.

**Non-Goals:** правило дубля вебхука, проверка «прогон вытеснен», поведение при включённом
авторевью и при выключенном репозитории не меняются.

## Decisions

### D1. Подстановка — только вместо упавшего или отменённого ручного прогона

Уточняет D1 `manual-gate-after-failed-relaunch`.

```
head_run_in_charge(uow, repository: Repository | None, merge_request_id, head_sha) -> ReviewRun | None
  latest = find_latest(merge_request_id, head_sha)
  repository is None:
      latest
  на паузе авторевью (connected_at задан, auto_review_enabled = false)
  и latest is not None
  и latest.trigger is MANUAL
  и latest.status in (FAILED, CANCELLED):
      find_completed_manual(merge_request_id, head_sha) or latest
  иначе latest
```

- Вебхуковый `latest` возвращается как есть, `pause_check` даёт check паузы — независимо от
  того, был ли раньше на head завершённый ручной прогон.
- Ручной `latest` в работе или завершённый возвращается как есть — `pause_check` даёт `None`,
  gate по его находкам.
- `repository = None` (репозитория нет в базе) — `find_latest`: так уже делала сверка gate после
  вебхука треда. Остальные места вызова при `repository is None` выходят сами (`return`,
  `LookupError`), поэтому прогон, который вернул помощник, там не используется.
- Альтернатива — оставить ветвление в местах вызова — повторяет одно и то же условие четырежды.

## Risks / Trade-offs

- [Retarget на паузе] M1 завершился против `main`, треды закрыты, PR перенаправлен на `release`,
  ручной M2 на тот же head падает → за head снова M1 → `success` «Ревью пройдено» для диффа,
  который против новой базы никто не проверял. До `manual-gate-after-failed-relaunch` здесь был
  check паузы. Принято: на паузе мердж и так не блокируется, неверен только текст check run.
- [Лишний запрос на паузе] → теперь только когда последний ручной прогон упал или отменён.
