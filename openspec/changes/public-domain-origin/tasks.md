## 1. Подготовка (блокер)

- [ ] 1.1 Выбран домен, A-запись указывает на `62.169.24.237`; проверка — `dig +short <домен>` отдаёт IP стенда
- [ ] 1.2 Админ App добавляет callback `https://<домен>/api/v1/auth/github/callback` вторым, callback по IP оставляет — до 2.2 (design D2); проверка — в настройках App оба Callback URL

## 2. Деплой бэкенда

- [x] 2.1 `.github/workflows/deploy.yml`: `TF_VAR_public_origin` и `PUBLIC_ORIGIN` job'а — `vars.PUBLIC_ORIGIN`, без неё `https://` + `VPS_DMC268_IP_T1` (design D1); формат — `validation` в `infra/variables.tf`, адрес без хоста (`https://` или пусто) — защита в `infra.sh`; проверка — `tofu test` (домен и IP проходят, битые форматы — отказ), pytest `test_targeted_plan_without_public_origin_is_not_built`, `zizmor` зелёный (`actionlint` локально не запускался — проверит CI)
- [ ] 2.2 `gh variable set PUBLIC_ORIGIN --body https://<домен>` после деплоя фронта на staging LE (порядок — design D2); проверка — сразу после деплоя `curl -sik -X POST "$PUBLIC_ORIGIN/api/v1/auth/logout" -H "Origin: $PUBLIC_ORIGIN"` → `204` (`403 ORIGIN_FORBIDDEN` — `PUBLIC_ORIGIN` не совпал с `PUBLIC_HOST` фронта)

## 3. GitHub App и документация

- [ ] 3.1 Сразу после перехода фронта на боевой LE админ App меняет только webhook URL на `https://<домен>/api/v1/webhooks/github` и Setup URL на `https://<домен>/api/v1/auth/github/setup` с включённым «Redirect on update» (callback не трогает — он добавлен в 1.2), затем Redeliver упавших за окно доставок в Recent Deliveries; проверка — вход через GitHub работает, Recent Deliveries 2xx
- [x] 3.2 `docs/GITHUB_APP.md`, `docs/testing/RELEASE_SMOKE.md`, `infra/README.md`: адрес сервиса — домен; проверка — `grep -rn 62.169.24.237` находит только упоминания сервера (SSH, запасной адрес по IP в `infra/tests/main.tftest.hcl`)

## 4. Итоговая проверка

- [ ] 4.1 `openssl s_client -connect <домен>:443` показывает сертификат Let's Encrypt на 90 дней; `curl https://<домен>/api/v1/status` → 200; RELEASE_SMOKE SM-04, SM-13, SM-15, SM-21 по домену проходят
- [ ] 4.2 Change заархивирован (`openspec archive public-domain-origin`), `openspec validate --all --strict` зелёный
