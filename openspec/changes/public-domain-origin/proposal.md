## Why

Бэкендовая половина ui#41 (подзадача #97) и DoD 2 задачи #386: стенд должен быть доступен проверяющим по публичному URL. Сейчас он отдаётся по `https://62.169.24.237`. На IP Let's Encrypt выдаёт только профиль `shortlived` (160 часов), сертификат продлевается раз в ~3 дня, а адрес — голый IP. С доменом сертификат выдаётся на 90 дней и продлевается раз в ~45.

**Блокер:** не выбран домен — свой или поддомен DuckDNS (аккаунт заводится вручную). Пока его нет, change не реализуется.

## What Changes

- `.github/workflows/deploy.yml`: `TF_VAR_public_origin` берётся из новой переменной репозитория `PUBLIC_ORIGIN` (`https://<домен>`), а не собирается из `VPS_DMC268_IP_T1`. Без этого изменяющие запросы с нового адреса получат `403 ORIGIN_FORBIDDEN`. SSH-шаги продолжают ходить по IP.
- GitHub App (делает админ App): callback `https://<домен>/api/v1/auth/github/callback`, webhook URL `https://<домен>/api/v1/webhooks/github`.
- Документация: `62.169.24.237` в `docs/GITHUB_APP.md`, `docs/testing/RELEASE_SMOKE.md`, `infra/README.md` заменяется доменом. Где речь о сервере, а не об адресе сервиса, IP остаётся.
- Порядок выкатки согласуется с `dmc-268-ui-t1` (убрать `profile shortlived require;`, домен в `PUBLIC_HOST`).

## Capabilities

### New Capabilities

Нет.

### Modified Capabilities

Нет — `skip_specs: true`: требования (`backend-delivery`: «API доступен снаружи только через публичный вход», HTTPS) от адреса не зависят.

## Impact

- `.github/workflows/deploy.yml`, переменная репозитория `PUBLIC_ORIGIN`, настройки GitHub App.
- `docs/GITHUB_APP.md`, `docs/testing/RELEASE_SMOKE.md`, `infra/README.md`.
- Окно выкатки: между деплоем фронта с доменом и деплоем бэкенда с новым origin изменяющие запросы отвечают `403`.
