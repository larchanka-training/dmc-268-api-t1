## ADDED Requirements

### Requirement: Стенд передаёт реквизиты GitHub App

Развёртывание SHALL передавать контейнерам `api` и `worker` идентификатор и приватный ключ GitHub App, а контейнеру `api` — ещё client ID и client secret OAuth того же App. Идентификаторы SHALL приходить из переменных репозитория, ключ и client secret — из секретов. Ключ и client secret SHALL NOT храниться в коммитах и SHALL NOT печататься в журнале развёртывания.

#### Scenario: Вебхук на стенде

- **WHEN** на стенд пришёл подписанный вебхук `pull_request` зарегистрированного репозитория
- **THEN** API получает метаданные PR через GitHub App, а не отвечает `502` из-за пустых реквизитов

#### Scenario: Ключ в журнале

- **WHEN** выполняется развёртывание
- **THEN** ни приватный ключ, ни client secret GitHub App не появляются в журнале job'а

#### Scenario: Вход на стенде

- **WHEN** пользователь открывает `/api/v1/auth/github/login` на стенде
- **THEN** его уводит на страницу авторизации GitHub с client ID нашего App, а не на страницу ошибки
