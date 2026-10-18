## 1. Развёртывание

- [x] 1.1 `.github/workflows/deploy.yml`: `TF_VAR_github_app_id` из `vars.APP_ID_DMC268_T1`, `TF_VAR_github_app_private_key` из `secrets.APP_PRIVATE_KEY_DMC268_T1`
- [x] 1.2 `infra/README.md`: обе переменные в таблице «Откуда»

## 2. Стенд

- [x] 2.1 Переменная `APP_ID_DMC268_T1` и секрет `APP_PRIVATE_KEY_DMC268_T1` заведены в настройках репозитория
- [ ] 2.2 Вебхук на стенде получает метаданные PR: ответ `202`, а не `502`
- [x] 2.3 Переменная `APP_CLIENT_ID_DMC268_T1` и секрет `APP_CLIENT_SECRET_DMC268_T1` заведены; callback `https://62.169.24.237/api/v1/auth/github/callback` зарегистрирован у GitHub App (перенесено из `auth-session`, 7.1)
- [x] 2.4 Вход через GitHub на стенде: cookie `session` поставлена, `/login` уводит в `/app/` (перенесено из `auth-session`, 7.3)
