## ADDED Requirements

### Requirement: Вебхук закрытия треда ревью

Эндпоинт приёма вебхуков SHALL обрабатывать событие GitHub
`pull_request_review_thread` с действиями `resolved` и `unresolved`: при валидной
подписи и зарегистрированном репозитории система SHALL обновить `thread_resolved`
у соответствующей записи `published_comments` по `comment.id` из payload и
ответить HTTP 202.

#### Scenario: Тред закрыт вручную на GitHub

- **WHEN** приходит `pull_request_review_thread` с `action=resolved` для комментария,
  опубликованного сервисом
- **THEN** у корневой inline-записи выставляется `thread_resolved=true`
