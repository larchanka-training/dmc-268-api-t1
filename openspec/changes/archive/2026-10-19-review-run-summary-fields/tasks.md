## 1. Реализация

- [x] 1.1 `merge_request_url` в `app/domain/provider_links.py` и её юнит-тест
- [x] 1.2 `PublishedCommentRepo.list_for_run` — порядок по `published_at`, затем по id, в адаптере и фейке; интеграционный тест
- [x] 1.3 `ReviewRunResponse`: `rejected_findings`, `merge_request`, `published_comments`; общая `MergeRequestBase` в `app/api/schemas.py`

## 2. Контракт и проверки

- [x] 2.1 Экспорт `docs/openapi/openapi.json`
- [x] 2.2 Тесты ручки на новые поля, включая `url: null` не для GitHub
- [x] 2.3 `ruff check`, `lint-imports`, `mypy`, `pytest`, `openspec validate --all --strict` проходят
