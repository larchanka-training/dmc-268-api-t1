Каждая задача: красно-зелёный виток (скилл `backend-tdd`).

## 1. Gate выметенного прогона

- [x] 1.1 `block_head_of_failed_run` (`app/application/review_pipeline.py`) принимает `run_id`, `head_sha`, `repository_full_name`, `installation_id` вместо `ReviewJob`; пайплайн зовёт её с полями сообщения, прежние тесты `tests/application/test_review_pipeline.py` зелёные
- [x] 1.2 `sweep_stale_runs` (`app/application/stale_sweep.py`) с `vcs` и `installations` после перевода в `failed` берёт номер установки по `full_name` репозитория прогона и зовёт `block_head_of_failed_run`; сбой каталога и `None` пишутся в лог. Тесты: `tests/application/test_stale_sweep.py`: прежнее ревью → `failure`; пауза → check паузы; первое ревью head и вытесненный прогон → ничего; сбой каталога, `None` установки, сбой check run → выметены все, счётчик верный
- [x] 1.3 `build_maintenance` (`app/worker/factory.py`) передаёт очистке `redelivery_vcs_gateway()` и `installation_catalog()` контейнера. Тест: `tests/worker/test_maintenance.py`
- [x] 1.4 После временного сбоя GitHub (`VcsTransientError`) на запросе номера установки или на записи check run проход очистки до конца не выставляет gate: проход идёт в consume-цикле воркера. `block_head_of_failed_run` возвращает `False` при временном сбое. Тест `test_github_down_stops_gate_lookups_for_the_rest_of_the_pass` (оба случая)

## 2. Проверки

- [x] 2.1 `uv run ruff check .`, `uv run lint-imports`, `uv run mypy .`, `uv run pytest`, `npx @fission-ai/openspec@1.15.0 validate --all --strict`
