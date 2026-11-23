## 1. Подтверждение

- [ ] 1.1 Красные тесты в `tests/worker/test_worker_loop.py`: короткий обработчик и короткий проход очистки подтверждаются заметно быстрее такта 1 с; `FakeConnection` (`tests/fakes/queue.py`) получает `add_callback_threadsafe`, а `process_data_events` ждёт событие до `time_limit`, как pika
- [ ] 1.2 Интеграционный тест (`integration`, настоящий RabbitMQ): воркер `build_worker` с коротким обработчиком подтверждает сообщение быстрее 0.5 с
- [ ] 1.3 `Worker._beside_connection`: в `finally` потока — `connection.add_callback_threadsafe(lambda: None)`, отказ закрытого соединения не роняет поток; комментарий у `_HANDLER_POLL_SECONDS`

## 2. Замер и документация

- [ ] 2.1 Полный профиль `Load` локально и на раннере (`gh workflow run load.yml --ref fix/worker-ack-latency`)
- [ ] 2.2 `docs/testing/RULES_E2E_REPORT.md` §4: новые цифры, причина и исправление вместо пункта о регрессе #401, остаток — по замеренному числу вызовов GitHub

## 3. Завершение

- [ ] 3.1 Проверки: `ruff`, `lint-imports`, `PYTEST_FAIL_ON_SKIP=1 pytest` с базой и брокером, `make cov-llm-schema`, `alembic heads`, `openspec validate --all --strict`
- [ ] 3.2 Change заархивирован (`openspec archive worker-ack-without-tick-delay`)
