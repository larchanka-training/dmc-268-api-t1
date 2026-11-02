# Tasks

## 1. Гонка задержанного POST

- [x] 1.1 Тесты на смену порога и повторное открытие треда: падают до правки
- [x] 1.2 `settle_merge_gate` сравнивает собранный check
- [x] 1.3 Тесты на потерянный ответ POST, сброс `settled` после сбоя
- [x] 1.4 Тест, что gate собран по находкам нового прогона

## 2. Пауза ревью

- [x] 2.1 Тесты на задержанный POST, завершение и падение прогона при паузе: падают до правки
- [x] 2.2 `pause_check` в `settle_merge_gate` и в `_block_head_of_failed_run`, в том числе без прежнего ревью

## 3. Проверка

- [x] 3.1 `ruff`, `mypy`, `lint-imports`, `pytest` с PostgreSQL и RabbitMQ, chaos, `openspec validate --all --strict`
