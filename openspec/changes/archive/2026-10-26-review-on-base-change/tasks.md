## 1. Красные тесты

- [x] 1.1 Домен: `edited` с `changes.base` - событие ревью, без него - `None` (`tests/domain/test_webhook.py`)
- [x] 1.2 Use case: смена базы после `completed` - новый прогон; во время активного - `cancelled` и новый; повтор на той же базе - дубль; прогон без базы - дубль (`tests/application/test_handle_webhook.py`)
- [x] 1.3 Сквозной: обход merge gate сменой целевой ветки закрыт (`tests/security/test_pr_manipulation_e2e.py`)

## 2. Приём

- [x] 2.1 Чистая функция `extract_github_event`: `edited` в `TRIGGERING_ACTIONS`, `_base_changed`
- [x] 2.2 `handle_webhook_event`: `_same_base` в проверке дубля, `_supersede` активного прогона на старой базе
