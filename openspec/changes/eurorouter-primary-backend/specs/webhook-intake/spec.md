## ADDED Requirements

### Requirement: Ревью запускается только для PR своих авторов

Событие `pull_request` SHALL запускать ревью, только если `author_association` автора PR — `OWNER`, `MEMBER` или `COLLABORATOR`. PR остальных авторов (`CONTRIBUTOR`, `FIRST_TIME_CONTRIBUTOR`, `FIRST_TIMER`, `NONE` и отсутствие поля) SHALL получать `202 ignored` без записей и задачи. Временная защита ключа модели до биллинга.

#### Scenario: PR коллаборатора

- **WHEN** приходит подписанный `pull_request` `opened` с `author_association = COLLABORATOR` для подключённого репозитория
- **THEN** прогон создаётся и задача ставится в очередь, как обычно

#### Scenario: PR из форка от постороннего

- **WHEN** приходит подписанный `pull_request` `opened` с `author_association = FIRST_TIME_CONTRIBUTOR`
- **THEN** ответ `202 ignored`, записей нет, очередь не трогается
