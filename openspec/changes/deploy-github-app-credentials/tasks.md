## 1. Развёртывание

- [x] 1.1 `.github/workflows/deploy.yml`: `TF_VAR_github_app_id` из `vars.GITHUB_APP_ID_DMC268_T1`, `TF_VAR_github_app_private_key` из `secrets.GITHUB_APP_PRIVATE_KEY_DMC268_T1`
- [x] 1.2 `infra/README.md`: обе переменные в таблице «Откуда»

## 2. Стенд

- [ ] 2.1 Переменная `GITHUB_APP_ID_DMC268_T1` и секрет `GITHUB_APP_PRIVATE_KEY_DMC268_T1` заведены в настройках репозитория
- [ ] 2.2 Вебхук на стенде получает метаданные PR: ответ `202`, а не `502`
