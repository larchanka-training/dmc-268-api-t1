## MODIFIED Requirements

### Requirement: Опубликованные комментарии отслеживаются

Система SHALL фиксировать каждый комментарий, опубликованный ею на хостинге системы
контроля версий, связывая его с исходным замечанием, если оно есть, и сохраняя
идентификатор опубликованного комментария у провайдера, его вид (`summary`, `inline`,
`thread_reply`), признак `thread_resolved`, ссылку `in_reply_to_provider_comment_id`
для ответов в тред и счётчики `feedback_positive` / `feedback_negative`.

#### Scenario: Ответ в тред записан отдельным видом

- **WHEN** бот публикует ответ в существующий инлайн-тред
- **THEN** запись имеет `kind=thread_reply` и `in_reply_to_provider_comment_id`
  корневого комментария

#### Scenario: Закрытие треда на хостинге отражается в базе

- **WHEN** тред закрыт на GitHub (ботом, вебхуком или сверкой перед merge gate)
- **THEN** у корневой записи inline `thread_resolved=true`
