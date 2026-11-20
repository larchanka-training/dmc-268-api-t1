## Why

Бэкендовая половина ui#41 (подзадача #97) и DoD 2 задачи #386: стенд должен быть доступен проверяющим по публичному URL. Сейчас он отдаётся по `https://62.169.24.237`. На IP Let's Encrypt выдаёт только профиль `shortlived` (160 часов), сертификат продлевается раз в ~3 дня, а адрес — голый IP. С доменом сертификат выдаётся на 90 дней и продлевается раз в ~45.

**Домен ещё не выбран** — свой или поддомен DuckDNS (аккаунт заводится вручную). Код от него не зависит: адрес параметризован переменной репозитория, а пока её нет, стенд работает по IP, как сейчас. Выбор домена блокирует только ручные задачи выкатки.

## What Changes

- `.github/workflows/deploy.yml`: `TF_VAR_public_origin` и `PUBLIC_ORIGIN` job'а берутся из новой переменной репозитория `PUBLIC_ORIGIN` (`https://<домен>`), а пока её нет — `https://` + `VPS_DMC268_IP_T1`, симметрично фронту. Без этого изменяющие запросы с нового адреса получат `403 ORIGIN_FORBIDDEN`. SSH-шаги продолжают ходить по IP.
- `infra/variables.tf`: проверка формата `public_origin` — пусто (локальный apply) или `https://<хост>` строчными (домен или IPv4) без порта, слеша и точки на конце, пути; `plan` с другим значением падает до изменений на сервере. `infra/locals.tf`: `trimsuffix` у адреса Grafana убран — слеша на конце больше не бывает.
- `.github/scripts/deploy/infra.sh`: защита — пустой адрес стенда (не заданы обе переменные) роняет шаг до первого `plan`.
- Summary деплоя: строка «Адрес» — `<PUBLIC_ORIGIN>/api/v1/`, а не `https://<IP>/api/v1/` (`scripts/deploy_report.py`).
- Тесты: `tofu test` на формат `public_origin` (домен и IP проходят; слеш, путь, `http://`, порт, заглавные, точка на конце — отказ), `tests/test_deploy_report.py` — `infra.sh` с пустым адресом и строка «Адрес» в summary.
- GitHub App (делает админ App): callback `https://<домен>/api/v1/auth/github/callback`, webhook URL `https://<домен>/api/v1/webhooks/github`, Setup URL `https://<домен>/api/v1/auth/github/setup` с «Redirect on update».
- Документация: `62.169.24.237` в `docs/GITHUB_APP.md`, `docs/testing/RELEASE_SMOKE.md`, `infra/README.md` заменяется ссылкой на `PUBLIC_ORIGIN`; в `infra/README.md` — раздел «Публичный адрес стенда» с переменной и порядком выкатки. Где речь о сервере, а не об адресе сервиса, IP остаётся.
- Порядок выкатки согласуется с `dmc-268-ui-t1` (убрать `profile shortlived require;`, домен в `PUBLIC_HOST`).

## Capabilities

### New Capabilities

Нет.

### Modified Capabilities

Нет — `skip_specs: true`: требования (`backend-delivery`: «API доступен снаружи только через публичный вход», HTTPS) от адреса не зависят.

## Impact

- `.github/workflows/deploy.yml`, `.github/scripts/deploy/infra.sh`, `scripts/deploy_report.py`, `infra/variables.tf`, `infra/locals.tf`, `infra/tests/`, `tests/test_deploy_report.py`; переменная репозитория `PUBLIC_ORIGIN`, настройки GitHub App.
- `docs/GITHUB_APP.md`, `docs/testing/RELEASE_SMOKE.md`, `infra/README.md`.
- Окно выкатки: между деплоем фронта с доменом и деплоем бэкенда с новым origin изменяющие запросы отвечают `403`; с деплоя фронта на домен до боевого сертификата горит `PublicEndpointDown` — на окно его глушат (design D2).
