Каждая задача: красно-зелёный виток (скилл `backend-tdd`).

## 1. Домен

- [x] 1.1 `estimate_run_cost(prompt_tokens, max_output_tokens, eur_per_token) -> Decimal` и `eur_per_token(cost_eur, tokens) -> Decimal` (нуль без истории), чистые функции (`app/domain/billing.py`). Тест: `tests/domain/test_billing.py` — формула D4, нулевая история, точность `Decimal`
- [x] 1.2 `CoverageReason.BALANCE`; переход `building_context → completed` (`app/domain/enums.py`, `app/domain/lifecycle.py`); поля `ReviewRun.payer_user_id`, `estimated_cost_eur`, `coverage_reason`, `Repository.payer_user_id` (`app/domain/entities.py`). Тест: `tests/domain/test_lifecycle.py`

## 2. Хранение

- [x] 2.1 Миграция `0020`: `balance_credits`, `repositories.payer_user_id`, `review_runs.payer_user_id`/`estimated_cost_eur`/`coverage_reason`, проверки и индексы (D1, D2); модели и мапперы (`alembic/versions/0020_add_account_balance.py`, `app/infrastructure/db/models.py`, `mappers.py`). Проверка: `alembic upgrade head && alembic downgrade -1`, `tests/db/test_drift.py`
- [x] 2.2 Порт `BillingRepo`: `lock_payer`, `balance`, `eur_per_token()`, `add_credit`; адаптер с `pg_advisory_xact_lock` и фейк (`app/application/ports/repositories.py`, `app/infrastructure/db/repositories.py`, `tests/fakes/unit_of_work.py`). Тест: `tests/db/test_adapters.py` — баланс по зачислениям, факту и резерву; цена по последним 50 прогонам с ненулевой стоимостью, любой модели; вторая транзакция ждёт блокировку плательщика

## 3. Плательщик

- [x] 3.1 Подключение, повторное включение и `setup` пишут плательщика, только если он пуст (`connect_repository.py`, `reconnect`/`add` адаптера). Тест: `tests/application/test_connect_repository.py` — первое подключение, включение другим пользователем, `setup`
- [x] 3.2 Use case `take_over_payment` и `POST /api/v1/repositories/{repository_id}/payer` с проверкой роли мимо кэша, `payer_login` в элементе списка и в ответе (`connect_repository.py`, `app/api/repositories.py`). Тест: `tests/test_repositories_api.py` — maintain, write → `403`, невидимый → `404`, `payer_login` в списке

## 4. Воркер

- [x] 4.1 Проверка баланса после сборки контекста: оценка, блокировка, резерв или отказ одной транзакцией; отказ → `completed`, `coverage_reason = 'balance'`, нулевой расход, без анализа (`review_pipeline.py`). Тест: `tests/application/test_review_pipeline.py` — хватает, не хватает, нет плательщика, нет истории, флаг выключен
- [x] 4.2 Резерв закрывается нулевым расходом при сбое, отмене и очистке до перехода в `analysing` (`_mark_failed`, `_record_cancelled_spend`, `sweep_stale_runs`). Тест: `tests/application/test_review_pipeline.py` — сбой и отмена до `analysing` освобождают оценку, таймаут и отмена в `analysing` без ответа её оставляют
- [x] 4.3 Gate: `completed` с `coverage_reason = 'balance'` → `failure` «Недостаточно средств» (`build_unpaid_check`, ветка в `settle_merge_gate`, `merge_gate_publish.py`). Тест: `tests/application/test_merge_gate_publish.py`
- [x] 4.4 Настройка `BILLING_ENABLED` (`app/config.py`, `.env.example`, `app/worker/factory.py`). Тест: `tests/test_config.py`
- [x] 4.5 Гонка на живой базе: два прогона одного плательщика на последние деньги, второй отказывает. Тест: `tests/db/` с `requires_db`

## 5. API и зачисление

- [x] 5.1 `balance_eur` в `GET /api/v1/auth/me` (`app/api/auth.py`). Тест: `tests/test_auth_api.py`
- [x] 5.2 `coverage_reason` в `GET /api/v1/reviews/{review_run_id}` (`app/api/reviews.py`). Тест: `tests/test_reviews_api.py`
- [x] 5.3 Use case `credit_balance` и команда `python -m app.worker.credit_balance`, make-цель `credit-balance` (`app/application/use_cases/billing.py`, `app/worker/credit_balance.py`, `Makefile`). Тест: `tests/application/test_billing.py` — зачисление по id GitHub, логин сменил владельца, неизвестный id, не число
- [x] 5.4 `uv run python scripts/export_openapi.py`; `docs/erd.md`

## 6. Правки по ревью #396

- [x] 6.1 Ручной прогон без денег на паузе и вне фильтра веток ведёт gate как упавший ручной (`_no_review`, `merge_gate_publish.py`). Тест: `tests/application/test_merge_gate_publish.py` — пауза без оплаченного, пауза после оплаченного M1
- [x] 6.2 Нулевой расход прогона без денег в спеках `review-data-model` и `review-rest-api` и в описаниях `tokens_used`, `usage` (`app/api/reviews.py`). Тест: `tests/test_reviews_api.py` — прогон в том виде, как его пишет `reserve_or_refuse`
- [x] 6.3 `coverage_reason` в `ReviewRunListItem` и событиях `run.*` (`app/api/reviews.py`). Тест: `tests/test_review_list_api.py`
- [x] 6.4 Зачисление по id GitHub вместо логина, `find_by_login` удалён (`billing.py`, `credit_balance.py`, `Makefile`). Тест: `tests/application/test_billing.py`, `tests/worker/test_credit_balance.py`
- [x] 6.5 Отказ без денег не пишется в `review_runs_total` (`review_pipeline.py`). Тест: `tests/application/test_review_pipeline.py`
- [x] 6.6 `DELETE /api/v1/repositories/{repository_id}/payer` только для текущего плательщика, `paid_repositories` в `GET /api/v1/auth/me` (`drop_payer`, `list_paid_by`, `app/api/repositories.py`, `app/api/auth.py`). Тест: `tests/test_repositories_api.py`, `tests/test_auth_api.py`, `tests/db/test_adapters.py`

## 7. `review-limits` и проверки

- [x] 7.1 Убрать квоту из change `review-limits`: proposal, design D5, требование «Суточная квота токенов репозитория», `quota` в причинах покрытия → `balance` из `account-balance`; задачи 1.1 (без `coverage_reason`) и 3.x; отметить 3.1 сделанной #375
- [x] 7.2 `uv run ruff check .`, `uv run lint-imports`, `uv run mypy .`, `uv run alembic heads`, `PYTEST_FAIL_ON_SKIP=1 uv run pytest`, `npx @fission-ai/openspec@1.15.0 validate --all --strict`
