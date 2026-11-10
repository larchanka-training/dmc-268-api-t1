## 1. Метрика

- [x] 1.1 Порт `run_outcome.py`: константа `RUN_REFUSED = "refused"`, докстринги без «отказ не пишется»
- [x] 1.2 `run_review` (`review_pipeline.py`): ветка отказа зовёт `run_outcome.run_finished(RUN_REFUSED)`; тест в `tests/application/test_review_pipeline.py`
- [x] 1.3 `prime_run_outcomes` (`telemetry.py`) заводит ряд `refused` нулём; тест в `tests/infrastructure/test_telemetry.py`

## 2. Алерты

- [x] 2.1 `ReviewRunsFailing` по `status=~"completed|failed"`; тест в `alerts.test.yml`: отказы не разбавляют долю
- [x] 2.2 `ReviewRunsRefused` (warning) в `alerts.yml`; тесты в `alerts.test.yml`: горит при массовом отказе, молчит при единичном
- [x] 2.3 Таблица алертов в `infra/README.md`

## 3. Завершение

- [x] 3.1 `openspec validate --all --strict`, архив change
