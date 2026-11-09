# Proposal

## Why

Прогон без денег (`review-billing`) в `review_runs_total` не пишется, чтобы в доле упавших не
прятать настоящие сбои. Из-за этого массовый отказ не виден: если отказывают все прогоны (ошибка
в оценке стоимости, плательщика сняли с популярного репозитория), счётчик просто падает к нулю,
`ReviewRunsFailing` молчит, а сигнал есть только в базе и в красных check run у пользователей
(#402, тред ревью PR #396).

## What Changes

- Прогон без денег пишет в `review_runs_total` исход `status="refused"`. Ряд заводится нулём при
  старте API и воркера, как `completed` и `failed`.
- `ReviewRunsFailing` считает долю упавших и порог «хотя бы три прогона» только по
  `completed` и `failed`: отказы её не разбавляют.
- Новый алерт `ReviewRunsRefused` (warning): за час больше половины исходов прогонов — отказы,
  и отказов хотя бы три.
- Требование «Прогон без денег не учитывается в исходах прогонов» заменено требованием об
  отдельном исходе.

## Capabilities

### New Capabilities

### Modified Capabilities

- `review-billing`: прогон без денег учитывается в `review_runs_total` отдельным исходом
  `refused`, у отказов свой алерт.

## Impact

- `app/application/review_pipeline.py` (ветка отказа), `app/application/ports/run_outcome.py`,
  `app/infrastructure/telemetry.py` (`prime_run_outcomes`).
- `infra/observability/alerts.yml`, `infra/observability/alerts.test.yml`, `infra/README.md`
  (таблица алертов).
- Страница статуса не меняется: `ReviewRunsRefused` не в `ALERT_RULES`, отказ не сбой сервиса.
- Панель «Исходы прогонов» в Grafana группирует по `status` и покажет `refused` сама.
