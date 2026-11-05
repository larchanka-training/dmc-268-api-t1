## Why

Ревью PR #335 (AndrewKonst, тред на `app/application/review_pipeline.py:175`). Авторевью на
паузе (`auto_review_enabled = false`). Ручной прогон M1 завершился с незакрытым `critical` — на
head `failure`. Повтор ревью того же коммита разрешён (D4 change `manual-review-launch`), и
повторный ручной прогон M2 падает, например по тайм-ауту модели. Упавший прогон берёт за head
последний прогон — M2, а упавший ручной на паузе по D11 даёт check паузы `success`. Согласование
после POST (`publish_pause_check`) смотрит только на публикующийся или завершённый последний
прогон, поэтому gate M1 не возвращается: незакрытый `critical` перестаёт блокировать мердж из-за
того, что повторное ревью упало.

## What Changes

- На паузе авторевью за gate head отвечает: последний прогон, если он ручной и не упал и не
  отменён; иначе — последний завершённый ручной прогон на этом head; иначе — check паузы.
- Правило в одном месте — `head_run_in_charge` в `app/application/merge_gate_publish.py`; им
  заменяется чтение прогона на head там, где по нему решается gate: `settle_merge_gate`,
  согласование `publish_pause_check`, упавший прогон, вебхук треда, вебхук PR.
- Проверка «прогон вытеснен новым» у упавшего прогона и правило дубля вебхука по-прежнему смотрят
  на настоящий последний прогон.
- Новый метод порта `ReviewRunRepo.find_completed_manual(merge_request_id, head_sha)`.
- Не меняется: выключенный репозиторий перекрывает любой прогон; вебхуковый прогон, начатый до
  паузы, перекрывается паузой; при включённом авторевью поведение прежнее.

## Capabilities

### New Capabilities

### Modified Capabilities

- `review-pipeline`: исключение паузы для ручного прогона распространяется на последний
  завершённый ручной прогон head, если более поздний ручной прогон упал или отменён.

## Impact

- `app/application/merge_gate_publish.py`, `app/application/review_pipeline.py`,
  `app/application/sync_thread_resolution.py`, `app/application/use_cases/handle_webhook.py`.
- Порт `app/application/ports/repositories.py` и адаптер `app/infrastructure/db/repositories.py`:
  новый запрос без миграции (индекс по `merge_request_id` уже сужает выборку).
- HTTP API и контракт не меняются.
