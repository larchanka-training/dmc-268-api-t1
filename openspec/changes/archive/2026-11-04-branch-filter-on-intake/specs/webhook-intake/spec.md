## MODIFIED Requirements

### Requirement: Выключенное автоматическое ревью

Вебхук `pull_request` репозитория, у которого включено ревью, но `auto_review_enabled = false`, SHALL получать HTTP 202 (ignored) без прогона и задачи, а на своём PR SHALL ставить на head проверку `dmc-268-ai-review` `success` со своим заголовком «Авторевью выключено в настройках» (best-effort), отличным от «Ревью выключено в сервисе» выключенного репозитория. Вебхук `pull_request_review_thread` такого репозитория SHALL записывать состояние треда и ставить ту же проверку вместо пересчёта.

#### Scenario: PR при выключенном автоматическом ревью

- **WHEN** приходит `pull_request.opened` или `synchronize` своего PR для репозитория с `auto_review_enabled = false`
- **THEN** ответ — HTTP 202 (ignored), прогон не создаётся, задача не ставится, на `head_sha` ставится check run `success` с заголовком «Авторевью выключено в настройках», а в тексте сказано, что автоматическое ревью включается в настройках репозитория в кабинете

#### Scenario: GitHub не принял проверку

- **WHEN** при `auto_review_enabled = false` GitHub не ответил на установку check run
- **THEN** ответ всё равно HTTP 202 (ignored), сбой пишется в лог, доставка не переотправляется

#### Scenario: Тред при выключенном автоматическом ревью

- **WHEN** у репозитория с `auto_review_enabled = false` человек закрывает тред бота и приходит `pull_request_review_thread` с `action=resolved`
- **THEN** ответ — HTTP 202 (acknowledged), у записи `published_comments` выставляется `thread_resolved=true`, на head ставится проверка `success` «Авторевью выключено в настройках», gate по тредам не пересчитывается, даже если другой тред не ниже порога открыт

#### Scenario: Выключенный репозиторий с выключенным авторевью

- **WHEN** репозиторий выключен `DELETE /api/v1/repositories/{repository_id}` и у него же `auto_review_enabled = false`, а приходит `pull_request.opened` своего PR
- **THEN** на head ставится проверка выключенного репозитория «Ревью выключено в сервисе»: выключение репозитория важнее настройки

#### Scenario: Включение обратно

- **WHEN** `PUT …/settings` вернул `auto_review_enabled = true`, и приходит `pull_request.synchronize`
- **THEN** создаётся прогон и ставится задача, как до выключения

#### Scenario: Пауза важнее фильтра веток

- **WHEN** у репозитория `auto_review_enabled = false` и `branch_filter = ["release/*"]`, а приходит `pull_request.opened` своего PR в `main`
- **THEN** на head ставится проверка «Авторевью выключено в настройках», а не «Ветка не в фильтре ревью»: пауза важнее фильтра

## ADDED Requirements

### Requirement: Фильтр целевых веток

Вебхук `pull_request` своего PR SHALL сверять целевую ветку из свежих метаданных PR с `branch_filter` репозитория (glob, как у файлов правил); пустой фильтр SHALL означать все ветки. PR вне фильтра SHALL получать HTTP 202 (ignored) без прогона и задачи, а на head SHALL ставиться проверка `success` «Ветка не в фильтре ревью» (best-effort). Уход PR из фильтра SHALL отменять его активный прогон, возврат в фильтр SHALL обрабатываться как смена целевой ветки.

#### Scenario: PR в ветку вне фильтра

- **WHEN** у репозитория `branch_filter = ["release/*"]`, а приходит `pull_request.opened` или `synchronize` своего PR в `main`
- **THEN** ответ — HTTP 202 (ignored), прогон не создаётся, задача не ставится, на `head_sha` ставится check run `success` «Ветка не в фильтре ревью», в лог пишутся id репозитория и номер PR атрибутами записи, без имени ветки

#### Scenario: Совпадение паттерна

- **WHEN** у репозитория `branch_filter = ["release/*"]`
- **THEN** ветка `release/1.2` подходит, а `release/1.2/hotfix` и `Release/1.2` нет; `release/**` подходит и к `release/1.2/hotfix`

#### Scenario: PR в ветку фильтра

- **WHEN** у репозитория `branch_filter = ["release/*"]`, а приходит `pull_request.opened` своего PR в `release/1.2`
- **THEN** создаётся прогон и ставится задача, как без фильтра

#### Scenario: PR увели из ветки фильтра

- **WHEN** у PR в `main` идёт прогон, `branch_filter = ["main"]`, и приходит `pull_request.edited` со сменой целевой ветки на `develop`
- **THEN** прогон отменяется, новый не создаётся, на head ставится check run `success` «Ветка не в фильтре ревью»

#### Scenario: PR вернули в ветку фильтра

- **WHEN** PR, уведённый из ветки фильтра, возвращают `edited` в ветку, подходящую под `branch_filter`, а на head есть завершённый или отменённый прогон
- **THEN** на head ставится блокировка «Целевая ветка сменилась — идёт повторное ревью» и создаётся прогон на текущий head: завершённый прогон дублем его не делает

#### Scenario: Тред у PR вне фильтра

- **WHEN** у PR в `main` при `branch_filter = ["release/*"]` человек закрывает тред бота и приходит `pull_request_review_thread` с `action=resolved`
- **THEN** ответ HTTP 202 (acknowledged), состояние треда записывается, на head ставится проверка `success` «Ветка не в фильтре ревью», gate по тредам не пересчитывается

#### Scenario: Ручной прогон вне фильтра

- **WHEN** целевая ветка PR вне `branch_filter`, а за head отвечает ручной прогон, который не упал и не отменён
- **THEN** на head остаётся его gate, а не проверка фильтра (требование «Прогон при паузе ревью не блокирует merge gate» спеки `review-pipeline`)
