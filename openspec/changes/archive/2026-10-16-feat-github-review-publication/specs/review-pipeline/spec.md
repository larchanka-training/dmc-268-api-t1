## ADDED Requirements

### Requirement: Публикация результатов прогона на хостинг

После анализа LLM воркер SHALL перевести прогон в `publishing` и опубликовать на
GitHub для соответствующего pull request: один итоговый комментарий прогона и не
более одного инлайн-комментария на каждое сохранённое замечание. Идентификаторы
инлайн-комментариев SHALL запрашиваться через
`GET /repos/{owner}/{repo}/pulls/{number}/reviews/{review_id}/comments`, а не
из тела ответа `POST …/reviews`. Каждая публикация SHALL быть записана в
`published_comments`.

#### Scenario: Инлайн-комментарии получают id с хостинга

- **WHEN** прогон публикует пакет инлайн-комментариев
- **THEN** для каждого замечания сохраняется `provider_comment_id` из списка
  комментариев созданного review

### Requirement: Диалог в тредах на повторном прогоне

На повторном прогоне для того же pull request система SHALL для каждого открытого
инлайн-треда (не `thread_resolved`) принять `threadFollowUps` от LLM, опубликовать
ответ через
`POST /repos/{owner}/{repo}/pulls/{number}/comments/{id}/replies` и при допустимом
статусе закрыть тред на хостинге.

#### Scenario: Ответ уходит в корневой комментарий

- **WHEN** модель возвращает follow-up для ранее опубликованного инлайн-комментария
- **THEN** ответ публикуется как reply к корневому `provider_comment_id` этого треда

### Requirement: Merge gate по критичным открытым тредам

Перед завершением публикации система SHALL сверить открытые треды с состоянием на
GitHub и SHALL выставить check run `dmc-268-ai-review` на `head_sha` с
`conclusion=failure`, если остаётся незакрытый тред с замечанием `critical`.

#### Scenario: Ручное закрытие треда снимает блокировку

- **WHEN** тред с critical был закрыт на GitHub вручную до merge gate
- **THEN** `thread_resolved` обновляется при сверке и check run получает `success`
