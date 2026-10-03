# Чек-лист согласования контрактов

## Frontend
- [ ] Полей `ReviewJob` достаточно для UI, включая `trigger`, `baseCommitSha`, `model`,
      `tokensUsed`, `durationSeconds`, `rejectedFindings`, `lastProgressAt`, статус `publishing`.
- [ ] Все loading/progress states понятны, включая `cancelled` и зависший (`lastProgressAt`
      давно не обновлялся) прогон до того, как его подберёт сборщик (§7.5).
- [ ] `Finding` содержит необходимые данные, включая `side`/`line` вместо диапазона строк и
      `confidence`.
- [ ] Error model можно корректно показать пользователю, включая `scmProvider` и `providerAttempt`.
- [ ] Согласован способ обновления статуса: polling (предложение по умолчанию для MVP) с опорой
      на `lastProgressAt`, или SSE.

## Backend
- [ ] Все endpoints реализуемы, включая `POST /webhooks/{provider}` с проверкой подписи.
- [ ] HTTP status codes согласованы: дубль нетерминального ReviewRun — `409` на
      `POST /reviews` и `202` с существующим ReviewJob на `POST /webhooks/{provider}` (§4).
- [ ] Префикс `/api` (`servers` в `openapi.yaml`) ставит приложение, прокси его не срезает.
- [ ] ReviewJob можно сохранить в текущей модели данных (сверено с `0001_baseline_schema.py`).
- [ ] Finding можно сохранить без потери информации, включая `side` (обязателен для
      `uq_findings_anchor`).
- [ ] Idempotency определена: `provider + providerRepositoryId + pullRequestNumber + headCommitSha`.

## Queue / Worker
- [ ] State Machine соответствует реальному worker flow (`app/domain/lifecycle.py`), включая
      `publishing` и `cancelled`.
- [ ] Retryable ошибки определены.
- [ ] Max `providerAttempt` определён, разведён с redelivery сообщения брокером.
- [ ] Timeout определён.
- [ ] Duplicate delivery безопасен.
- [ ] Visibility timeout и dead-letter policy в RabbitMQ определены.
- [ ] Лимит устаревания зависшего прогона (§7.5) определён.
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
