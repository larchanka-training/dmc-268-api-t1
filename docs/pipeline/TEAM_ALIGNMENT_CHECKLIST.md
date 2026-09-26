# Чек-лист согласования контрактов

## Frontend
- [ ] Полей `ReviewJob` достаточно для UI.
- [ ] Все loading/progress states понятны.
- [ ] `Finding` содержит необходимые данные.
- [ ] Error model можно корректно показать пользователю.
- [ ] Согласован способ обновления статуса: polling/SSE/другое.

## Backend
- [ ] Все endpoints реализуемы.
- [ ] HTTP status codes согласованы.
- [ ] ReviewJob можно сохранить в текущей модели данных.
- [ ] Finding можно сохранить без потери информации.
- [ ] Idempotency определена.

## Queue / Worker
- [ ] State Machine соответствует реальному worker flow.
- [ ] Retryable ошибки определены.
- [ ] Max attempts определён.
- [ ] Timeout определён.
- [ ] Duplicate delivery безопасен.
- [ ] FAILED jobs диагностируемы.

## Context Engine
- [ ] Формат входного diff согласован.
- [ ] Line numbers можно корректно сопоставить.
- [ ] Large/binary files имеют определённое поведение.
- [ ] Context limits определены.

## LLM / Agentic
- [ ] Structured output поддерживается.
- [ ] Finding Schema реализуема.
- [ ] Invalid JSON обработан.
- [ ] Schema validation error обработан.
- [ ] Provider timeout определён.
- [ ] Provider-specific поля не протекают в API.

## QA
- [ ] Happy path тестируем.
- [ ] Каждый state transition тестируем.
- [ ] Retry сценарии тестируемы.
- [ ] Invalid LLM output тестируем.
- [ ] Provider outage тестируем.
- [ ] Contract/API schema validation можно добавить в CI.

## Финальное согласование
- [ ] Frontend approved.
- [ ] Backend approved.
- [ ] Queue approved.
- [ ] Context Engine approved.
- [ ] LLM approved.
- [ ] QA approved.
- [ ] Tech Lead пометил контракт как v1.
