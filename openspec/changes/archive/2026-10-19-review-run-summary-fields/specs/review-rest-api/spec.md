## ADDED Requirements

### Requirement: Прогон ревью отдаётся со сводкой запроса на изменение и публикациями

`GET /api/v1/reviews/{review_run_id}` SHALL отдавать вместе с прогоном число отсеянных находок, сводку его запроса на изменение с репозиторием и ссылкой у провайдера и опубликованные прогоном комментарии по времени публикации, затем по id. Ссылка SHALL быть `null` у провайдера, формат ссылок которого не поддержан.

#### Scenario: Прогон по запросу на изменение в GitHub

- **WHEN** вызывается `GET /api/v1/reviews/{review_run_id}` для прогона по pull request `octo-org/api#42`
- **THEN** `merge_request.url` — `https://github.com/octo-org/api/pull/42`, а `merge_request.repository.full_name` — `octo-org/api`

#### Scenario: Провайдер без поддержанного формата ссылки

- **WHEN** запрос на изменение прогона живёт не в GitHub
- **THEN** `merge_request.url` — `null`

#### Scenario: Опубликованные комментарии в порядке публикации

- **WHEN** прогон опубликовал сводку, а затем комментарий к строке
- **THEN** `published_comments` содержит сначала сводку, затем комментарий к строке

#### Scenario: Прогон ещё ничего не опубликовал

- **WHEN** запрошен прогон без опубликованных комментариев
- **THEN** `published_comments` — пустой список
