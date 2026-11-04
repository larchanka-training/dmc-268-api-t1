## ADDED Requirements

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

#### Scenario: Фильтр веток не влияет на приём

- **WHEN** у репозитория `branch_filter = ["release/*"]`, а приходит `pull_request.opened` своего PR в `main`
- **THEN** прогон создаётся, как без фильтра: фильтр веток пока не применяется
