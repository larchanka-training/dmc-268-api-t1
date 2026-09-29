## 1. Порты и адаптеры

- [x] 1.1 `app/application/ports/repositories.py`: добавить в
      `RepositoryRepo` метод `list_all(limit: int, offset: int) ->
      list[Repository]` и в `MergeRequestRepo` — `list_for_repository(
      repository_id: UUID, limit: int, offset: int) -> list[MergeRequest]`.
- [x] 1.2 `app/infrastructure/db/repositories.py`:
      `SqlAlchemyRepositoryRepo.list_all` (`ORDER BY created_at`,
      `.limit().offset()`), `SqlAlchemyMergeRequestRepo.list_for_repository`
      (`ORDER BY number`, `.limit().offset()`). Фейки в
      `tests/fakes/unit_of_work.py` обновлены тем же контрактом
      (сортировка + срез списком).
- [x] 1.3 `tests/db/test_pagination.py` (`integration`, `requires_db`):
      несколько репозиториев/PR, `limit`/`offset` возвращают ожидаемые
      срезы в ожидаемом порядке. Проверено вживую против
      `postgres:18-alpine`.

## 2. Роутеры

- [x] 2.1 `app/api/repositories.py`: `GET /api/v1/repositories`, query
      `limit`/`offset` (дефолт 50, потолок 200 — приведение, не 400),
      Pydantic response model.
- [x] 2.2 `app/api/pull_requests.py`: `GET
      /api/v1/repositories/{repository_id}/pull-requests` — 404 через
      `repositories.get(repository_id) is None`, иначе
      `merge_requests.list_for_repository`.
- [x] 2.3 `app/api/reviews.py`: `GET /api/v1/reviews/{review_run_id}` — 404
      через `review_runs.get(review_run_id) is None`, иначе тело из
      `ReviewRun` + `findings.list_for_run`.
- [x] 2.4 Подключить все три роутера в `create_app`
      (`app/api/factory.py`).

## 3. Тесты роутеров (без БД, фейковый `UnitOfWork`)

- [x] 3.1 `tests/test_repositories_api.py`: список, дефолтный `limit`,
      `limit` выше потолка не отклоняется.
- [x] 3.2 `tests/test_pull_requests_api.py`: список для существующего
      репозитория; 404 для несуществующего.
- [x] 3.3 `tests/test_reviews_api.py`: прогон с находками; прогон без
      находок — пустой список, не ошибка; 404 для несуществующего прогона.

## 4. Финальная проверка

- [x] 4.1 `uv run pytest && uv run ruff check . && uv run lint-imports &&
      uv run mypy .` — зелено. `TEST_DATABASE_URL`+`TEST_RABBITMQ_URL`
      против живых Postgres/RabbitMQ: 159 passed, 0 skipped.
- [x] 4.2 Сквозная проверка вживую: `docker compose up -d --build`,
      подписанный вебхук завёл репозиторий/PR/прогон; `curl
      /api/v1/repositories` → 200 с записью; `.../pull-requests` → 200 со
      списком, 404 на случайном id; `/api/v1/reviews/{id}` → 200 с
      `status: completed`, `model: stub-llm`, `findings: []`, 404 на
      случайном id.
