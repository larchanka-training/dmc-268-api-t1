## Why

`infra/main.tf` передаёт API и воркеру `GITHUB_APP_ID` и `GITHUB_APP_PRIVATE_KEY`, но `deploy.yml` не задаёт `TF_VAR_github_app_id` и `TF_VAR_github_app_private_key`, и на стенд уходят пустые значения. Настройки опциональны, поэтому старт проходит, а VCS-шлюз падает при первом обращении: вебхук отвечает `502`, воркер не получает дифф. Спека `backend-delivery` требует передавать только настройки без значений по умолчанию, и пробел ничем не ловится.

## What Changes

- `deploy.yml` передаёт `TF_VAR_github_app_id` из переменной репозитория `GITHUB_APP_ID_DMC268_T1` и `TF_VAR_github_app_private_key` из секрета `GITHUB_APP_PRIVATE_KEY_DMC268_T1`.
- `infra/README.md`: обе переменные в таблице «Откуда».
- `backend-delivery`: требование — стенд передаёт API и воркеру реквизиты GitHub App из настроек репозитория, приватный ключ — только из секретов.

## Capabilities

### New Capabilities

### Modified Capabilities

- `backend-delivery`: стенд передаёт реквизиты GitHub App контейнерам, которые ходят в GitHub.

## Impact

- `.github/workflows/deploy.yml`, `infra/README.md`.
- Настройки репозитория: переменная `GITHUB_APP_ID_DMC268_T1` и секрет `GITHUB_APP_PRIVATE_KEY_DMC268_T1` заводит администратор GitHub App.
- Код приложения и `infra/*.tf` не меняются: переменные и окружение контейнеров уже есть.
