## 1. Спека и тест

- [x] 1.1 В требование «Изменяющие запросы проверяют Origin» добавлены порядок проверок и сценарий «Чужой Origin без сессии»
- [x] 1.2 Тест `test_a_mutating_request_from_elsewhere_without_a_session_is_403` в `tests/test_auth_api.py`
- [x] 1.3 `openspec validate --all --strict` проходит
