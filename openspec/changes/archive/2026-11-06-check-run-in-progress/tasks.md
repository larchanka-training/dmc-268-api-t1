# Tasks

## 1. Подготовка

- [x] 1.1 Ветка обновлена до `develop` после мерджа #356 и #360 (merge, без переписывания истории); дельта «Упавший прогон блокирует merge gate» сверена с текстом спеки после #356, `openspec validate --all --strict` проходит

## 2. Решение gate

- [x] 2.1 `MergeGateCheck.conclusion: str | None` (`None` означает `in_progress`), `run_id` параметром `set_merge_gate_check`; адаптер GitHub шлёт `status: in_progress` без `conclusion` и `details_url` из `public_origin`; тест адаптера на фейке GitHub API: тело запроса со ссылкой, без неё при пустом `public_origin` и у check паузы
- [x] 2.2 Правило D1 в `settle_merge_gate`: `in_progress` для прогона без ревью, `failure` для `failed` и `cancelled`; тесты на захват, упавшее первое ревью и отменённый прогон
- [x] 2.3 `settle_merge_gate` без `run_id`; тесты гонок (#325, #330, #335, #360) зелёные, ожидания сменились только там, где блокировка смены ветки стала `in_progress` и захват ставит `in_progress` первым

## 3. Пути прогона

- [x] 3.1 Захват в воркере зовёт пересчёт, сбой GitHub пишется в лог; тест: на head `in_progress`, потом итог; при отказе GitHub прогон завершается `completed`
- [x] 3.2 Упавший прогон и очистка зовут пересчёт вместо `_block_head_of_failed_run`; тест «Первое ревью коммита упало» даёт `failure`, тест #356 на первый выметенный прогон переписан под `failure`
- [x] 3.3 Приём при смене ветки зовёт пересчёт вместо `build_retarget_check`, 502 и откат при сбое сохранены; после `_fail_unenqueued` зовётся пересчёт; тесты сценариев `webhook-intake` «Смена целевой ветки блокирует merge gate до нового ревью»
- [x] 3.4 `build_retarget_check` удалён, `block_head_of_failed_run` только проверяет «последний на head» и зовёт пересчёт

## 4. Документация и проверка

- [x] 4.1 `README.md`, `docs/SYSTEM_DESIGN.md` и `docs/testing/SECURITY_AND_REGRESSION_REPORT.md`: в описании merge gate: `in_progress` с захвата, ссылка на прогон, упавший прогон всегда `failure`
- [x] 4.2 `ruff`, `mypy`, `lint-imports`, `PYTEST_FAIL_ON_SKIP=1 pytest` с PostgreSQL и RabbitMQ, chaos-сьют, `openspec validate --all --strict`
- [x] 4.3 Проверка на стенде после деплоя вынесена в DoD #295 (плашка «Идёт ревью» → итог, «Details» открывает прогон): change архивируется в этом PR, чтобы спеки после мерджа описывали новое поведение

## Workflow follow-up

- Архив change в том же PR до мерджа.
