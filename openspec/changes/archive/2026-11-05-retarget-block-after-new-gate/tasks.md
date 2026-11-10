Каждая задача: красно-зелёный виток (скилл `backend-tdd`).

## 1. Поправка gate

- [x] 1.1 `settle_merge_gate` собирает gate нового прогона, если тот `publishing` и его ревью записано (`app/application/merge_gate_publish.py`). Тест: `tests/application/test_publish_review.py`, `test_a_late_retarget_block_yields_to_the_gate_of_a_review_that_finished_publishing`: блокировка дошла после gate нового прогона → поправка его итогом
- [x] 1.2 Перебор порядков шагов старого прогона, приёма смены ветки и нового прогона: последним на head всегда gate нового прогона. Тест: `tests/application/test_publish_review.py`, `test_every_order_of_a_retarget_ends_with_the_gate_of_the_new_run` (без находок и с `critical`)

## 2. Проверки

- [x] 2.1 `uv run ruff check .`, `uv run lint-imports`, `uv run mypy .`, `uv run pytest`, `npx @fission-ai/openspec@1.15.0 validate --all --strict`
