# Tasks

## 1. Обвязка

- [x] 1.1 Опция `--responses DIR` раннера `tests/eval/runner.py` и `load_dataset(root, responses=...)` в `tests/eval/dataset.py` (D5); проверка — без опции снимок офлайн-отчёта байт-в-байт прежний, копия фикстур в `DIR/<case_id>/run-1.json` даёт те же метрики, нет каталога кейса — `DatasetError` и код 2
- [x] 1.2 `FakeGitHub.open_pull_from_diff(...)` в `tests/fakes/github_api.py` (D1); проверка — дифф отдаётся как есть, комментарий вне hunk'а — 422, head-файлы только из `head_files`
- [x] 1.3 `tests/eval/live.py`: `live_gateway(settings)` (rules-eval переиспользует), `RecordingLlmGateway`, `run_gold(...)` (PR через `open_pull_from_diff` с `**details` из `mr.json` кейса: `title`→`title`, `description`→`body`, `source_branch`→`head_ref`, `target_branch`→`base_ref`, D1), `write_artifact(...)` (D2–D4); тест обвязки `tests/eval/test_live_runner.py` на модели-оракуле: выбранные кейсы (bug, negative, adversarial включая ADV-08, TC-25) копируются в мини-датасет под `tmp_path`, `run_gold` гоняется на нём, `load_dataset(mini, responses=pass1|pass2)` — против мини-датасета, не полного набора; проверка — описание PR из `mr.json` доходит до промпта `REVIEW` (ADV-08), pass1 равен фикстуре, pass2 без отсеянного и отброшенного верификатором, сбой кейса даёт заглушки и статус, артефакт грузится раннером
- [x] 1.4 `tests/eval/test_live_gold.py` (маркер `eval`, `EVAL_REPETITIONS`, `EVAL_REPORT_DIR`; набор всегда полный — поднабора кейсов нет), описание маркера в `pyproject.toml`; проверка — без кредов пропуск с причиной, основной `uv run pytest` его не собирает

## 2. Отчёт и CI

- [x] 2.1 Генератор `tests/eval/benchmark_report.py` и тесты на фикстуре-артефакте (D6); проверка — вывод детерминирован, Mermaid `xychart-beta` согласован с осями, определения FPR и FDR на месте, ручные разделы сохраняются
- [x] 2.2 `eval.yml`: input `suite`, job `rules` → `tests/eval/rules`, job `gold` только `workflow_dispatch`; цели Makefile `eval-live-gold` и `benchmark-report` (D7, D8); проверка — `make benchmark-report EVAL_LIVE_DIR=<артефакт-фикстура> EVAL_DATASET=<её мини-датасет>` без сети, метка `eval` запускает только `rules`

## 3. Документация

- [x] 3.1 `docs/testing/RELEASE_SMOKE.md`, актуализация `docs/testing/LLM_EVAL.md` §4 и `docs/testing/TEST_PLAN.md` §3.5; проверка — `tests/test_docs.py` зелёный
- [x] 3.2 Каркас `BENCHMARK_REPORT.md` — выход генератора с пометкой «черновик» и ручными разделами; проверка — регенерация не трогает ручные разделы
- [x] 3.3 Архивация change (`openspec archive add-live-gold-benchmark`) в том же PR; проверка — `npx @fission-ai/openspec@1.15.0 validate --all --strict`
