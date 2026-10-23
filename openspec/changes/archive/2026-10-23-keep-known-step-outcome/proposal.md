## Why

Ревью PR #90 (larchanka-training/dmc-268-api-t1#88): если исход упавшего шага не записался, перевод прогона в `failed` закрывает шаг кодом `REVIEW_RUN_STALE`, хотя воркер знает настоящий код сбоя (например, `LLM_UNAVAILABLE`), длительность и токены шага.

## What Changes

- `StepRecorder` хранит исходы шагов, чья запись не удалась (`unwritten`), и `_mark_failed` передаёт их в `close_abandoned_steps`: такой шаг закрывается своим исходом, а не `REVIEW_RUN_STALE`.
- `REVIEW_RUN_STALE` остаётся у шагов, исход которых неизвестен: брошенная попытка умершего воркера, шаги выметенного прогона.

## Impact

- Код: `app/domain/run_steps.py`, `app/application/{review_run_steps,review_pipeline}.py`.
- Схема базы и контракт API не меняются.
