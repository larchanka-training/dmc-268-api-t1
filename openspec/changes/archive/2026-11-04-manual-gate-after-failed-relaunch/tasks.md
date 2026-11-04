Каждая задача — красно-зелёный виток (скилл `backend-tdd`). Решения — `design.md` (D1–D3).

## 1. Порт и адаптер

- [x] 1.1 Метод порта `ReviewRunRepo.find_completed_manual(merge_request_id, head_sha)` (`app/application/ports/repositories.py`), адаптер `SqlAlchemyReviewRunRepo.find_completed_manual` (`app/infrastructure/db/repositories.py`), фейк в `tests/fakes/unit_of_work.py`. Тест: `tests/db/test_webhook_idempotency.py` — только `manual`, только `completed`, самый поздний, чужой head не виден (маркер `integration`)

## 2. Правило в одном месте

- [x] 2.1 `head_run_in_charge(uow, repository, merge_request_id, head_sha)` (`app/application/merge_gate_publish.py`, D1); `settle_merge_gate` и согласование `publish_pause_check` берут прогон через него. Тест: `tests/application/test_merge_gate_publish.py` — упавший и отменённый M2 после завершённого M1 → M1; завершённый вебхуковый до паузы → прогон не меняется, check паузы; выключенный репозиторий → `DISABLED_REVIEW_CHECK`; задержанный POST паузы при упавшем M2 → gate M1
- [x] 2.2 Упавший прогон на паузе с завершённым ручным прогоном раньше на head — `settle_merge_gate` его gate (`app/application/review_pipeline.py`, D2). Тест: `tests/application/test_review_pipeline.py` — `[re-review]` ждёт gate M1; с критическим тредом M1 — `failure`; `[first-review]` — check паузы
- [x] 2.3 Вебхук треда (`app/application/sync_thread_resolution.py`, D2). Тест: `tests/application/test_sync_thread_resolution.py` — закрытие последнего треда M1 после упавшего M2 → `success` «Ревью пройдено»
- [x] 2.4 Вебхук PR (`app/application/use_cases/handle_webhook.py`, D2). Тест: `tests/application/test_handle_webhook.py` — переотправка события PR на head, где M2 упал после M1, → `ignored` без check паузы
