# Tasks

## 1. Подготовка

- [ ] 1.1 Ветка перебазирована на `develop` после мерджа #356 и #360; дельта «Упавший прогон блокирует merge gate» сверена с текстом спеки после #356, `openspec validate --all --strict` проходит

## 2. Решение gate

- [ ] 2.1 `MergeGateCheck`: `conclusion: str | None` (`None` означает `in_progress`) и `run_id: UUID | None`; адаптер GitHub шлёт `status: in_progress` без `conclusion` и `details_url` из `public_origin`; тест адаптера на фейке GitHub API: тело запроса со ссылкой, без неё при пустом `public_origin` и у check паузы
- [ ] 2.2 Чистая функция `merge_gate_for_head` в `app/domain/merge_gate.py` по таблице D1; юнит-тест на каждую строку таблицы без базы и сети
- [ ] 2.3 `settle_merge_gate` без `run_id` на `merge_gate_for_head`; существующие тесты гонок (#325, #330, #335, #360) зелёные без правок ожиданий, кроме текста блокировки смены ветки

## 3. Пути прогона

- [ ] 3.1 Захват в воркере зовёт пересчёт, сбой GitHub пишется в лог; тест: на head `in_progress`, потом итог; при отказе GitHub прогон завершается `completed`
- [ ] 3.2 Упавший прогон и очистка зовут пересчёт вместо `_block_head_of_failed_run`; тест «Первое ревью коммита упало» даёт `failure`, тест #356 на первый выметенный прогон переписан под `failure`
- [ ] 3.3 Приём при смене ветки зовёт пересчёт вместо `build_retarget_check`, 502 и откат при сбое сохранены; `_fail_unenqueued` зовёт пересчёт; тесты сценариев `webhook-intake` «Смена целевой ветки блокирует merge gate до нового ревью»
- [ ] 3.4 `_block_head_of_failed_run` и `build_retarget_check` удалены, `grep` по ним пуст

## 4. Документация и проверка

- [ ] 4.1 `docs/SYSTEM_DESIGN.md` и `docs/BACKEND_ARCHITECTURE.md`: в описании merge gate: `in_progress` с захвата, ссылка на прогон, упавший прогон всегда `failure`
- [ ] 4.2 `ruff`, `mypy`, `lint-imports`, `PYTEST_FAIL_ON_SKIP=1 pytest` с PostgreSQL и RabbitMQ, chaos-сьют, `openspec validate --all --strict`
- [ ] 4.3 На стенде, на живом PR: плашка проходит «Идёт ревью» → итог, «Details» открывает прогон в кабинете (DoD #295)

## Workflow follow-up

- Архив change в том же PR до мерджа.
