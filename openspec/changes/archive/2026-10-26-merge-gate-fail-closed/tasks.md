## 1. Красные тесты

- [x] 1.1 Приём: смена ветки выставляет `failure` до нового прогона; сбой блокировки - 502 без записей; первая доставка и повтор gate не трогают; `edited` и прогон без базы - не дубль (`tests/application/test_handle_webhook.py`)
- [x] 1.2 Пересчёт: ревью head идёт или упало - gate не выставляется; последний `completed` - выставляется (`tests/application/test_sync_thread_resolution.py`)
- [x] 1.3 Публикация: `cancelled` в базе - gate не выставляется (`tests/application/test_publish_review.py`)
- [x] 1.4 Сквозной: после `edited` на head сразу `failure` (`tests/security/test_pr_manipulation_e2e.py`)

## 2. Реализация

- [x] 2.1 `ReviewRunRepo.find_latest`: SQL-адаптер с интеграционным тестом, фейк
- [x] 2.2 Чистая `build_retarget_check`; `handle_webhook_event` - блокировка между транзакциями, `_same_base(…, action)`, `_supersede` перечитывает строку
- [x] 2.3 `refresh_merge_gate_for_merge_request` по `find_latest`; `publish_review` перечитывает статус
