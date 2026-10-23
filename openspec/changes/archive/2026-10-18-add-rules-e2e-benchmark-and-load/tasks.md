# Tasks

## 1. Статус `empty`

- [x] 1.1 `app/domain/enums.py`: `RulesStatus.EMPTY`; `app/application/repo_config_loader.py` (`load_repo_rules`) выдаёт его, когда разбор не принял ни одного правила, с отклонёнными разделами; тесты в `tests/application/test_repo_config_loader.py`
- [x] 1.2 `app/domain/prompt.py` (`_rules_message`): причина фоллбэка для `empty`; тест в `tests/domain/test_prompt.py`; докстринг `RepoRules` в `app/domain/entities.py`

## 2. Сквозной стенд и E2E

- [x] 2.1 `tests/fakes/github_api.py`: `FakeGitHub` - in-memory GitHub REST за `httpx.MockTransport` (адаптер без логики, только перевод HTTP в состояние)
- [x] 2.2 `tests/e2e/stack.py`: сборка стенда - контейнер с настоящими адаптерами базы, очереди и GitHub, воркер, подпись вебхука
- [x] 2.3 `tests/e2e/test_rules_e2e.py`: конфигурации `absent`, `rules`, `empty`, `adversarial` и PR, меняющий правила

## 3. Eval Precision/Recall

- [x] 3.1 `tests/eval/scoring.py`, `tests/eval/report.py`: чистые функции сопоставления, метрик и отчёта; тесты в `tests/eval/test_scoring.py`, `tests/eval/test_report.py`
- [x] 3.2 `tests/eval/rules/corpus/`, `tests/eval/rules/configs/`: корпус кейсов и конфигурации правил; `tests/eval/rules/corpus.py` - загрузка корпуса, `test_corpus.py` - его целостность
- [x] 3.3 `tests/eval/rules/runner.py` и `oracle.py`: прогон корпуса через стенд; `test_runner.py` - обвязка на оракуле в CI; `test_rules_eval.py` (маркер `eval`) - живая модель, отчёт JSON и Markdown
- [x] 3.4 `.github/workflows/eval.yml`

## 4. Нагрузка

- [x] 4.1 `tests/load/scenario.py`: сценарий (uvicorn, пул отправителей, потоки воркеров) и чистые функции метрик; тесты метрик
- [x] 4.2 `tests/load/test_load_smoke.py` (в CI) и `tests/load/test_load.py` (маркер `load`, отчёт и пороги)
- [x] 4.3 `.github/workflows/load.yml`

## 5. Документация

- [x] 5.1 `docs/testing/RULES_E2E_REPORT.md`: сценарии, результаты E2E, eval и нагрузки; ссылки из `README.md` и `docs/testing/TEST_PLAN.md`

## Workflow follow-up

- Заархивировать change в этом же PR до мерджа.
