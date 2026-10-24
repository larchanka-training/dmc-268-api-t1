# Tasks

## 1. Мёртвый код

- [x] 1.1 Удалить `anchor_matches_llm` и `_line_for_side`; вызовы `_line_for_side` заменить на `.line`
- [x] 1.2 Убрать параметр `blocking=` и `BLOCKING_SEVERITIES`: блокирует только `CRITICAL`
- [x] 1.3 Удалить `RepositoryRepo.list_all` из порта, адаптера и фейка и тесты, которые проверяли только его

## 2. Строка якоря

- [x] 2.1 Добавить свойство `DiffAnchor.line` и перевести на него `publish_review.py`, `prompt.py`, `diff.py`

## 3. Документация

- [x] 3.1 Поправить docstring `job_queue.py` и `llm_gateway.py`: порты входят в `ports.__all__`
- [x] 3.2 Привести дерево файлов в `docs/pipeline/TECH_LEAD_SPRINT_PLAN.md` к `docs/openapi/`

## 4. Зависимости и CI

- [x] 4.1 Добавить `httpx2` в extra `dev` через `uv`; тест про логи принимает логгер `httpx2`
- [x] 4.2 Добавить `uv lock --check` в job `lint` в `ci.yml`

## 5. Тесты

- [x] 5.1 Тест ухода клиента из потока событий проверяет, что подписка закрыта
- [x] 5.2 Полный `uv run pytest` без предупреждения `httpx` и без пропусков с базой и брокером
