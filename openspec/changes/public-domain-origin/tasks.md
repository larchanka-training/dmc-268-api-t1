## 1. Подготовка (блокер)

- [ ] 1.1 Выбран домен, A-запись указывает на `62.169.24.237`; проверка — `dig +short <домен>` отдаёт IP стенда

## 2. Деплой бэкенда

- [ ] 2.1 `.github/workflows/deploy.yml`: `TF_VAR_public_origin: ${{ vars.PUBLIC_ORIGIN }}` и проверка непустого значения в шаге проверки конфигурации; проверка — `actionlint`/`zizmor` зелёные, ручной запуск без переменной падает до apply
- [ ] 2.2 `gh variable set PUBLIC_ORIGIN --body https://<домен>` после деплоя фронта на staging LE (порядок — design D2); проверка — изменяющий запрос с нового origin не получает `403 ORIGIN_FORBIDDEN`

## 3. GitHub App и документация

- [ ] 3.1 Админ App меняет callback на `https://<домен>/api/v1/auth/github/callback` и webhook URL на `https://<домен>/api/v1/webhooks/github`; проверка — вход через GitHub работает, Recent Deliveries 2xx
- [x] 3.2 `docs/GITHUB_APP.md`, `docs/testing/RELEASE_SMOKE.md`, `infra/README.md`: адрес сервиса — домен; проверка — `grep -rn 62.169.24.237` находит только упоминания сервера (SSH)

## 4. Итоговая проверка

- [ ] 4.1 `openssl s_client -connect <домен>:443` показывает сертификат Let's Encrypt на 90 дней; `curl https://<домен>/api/v1/status` → 200; RELEASE_SMOKE SM-04, SM-13, SM-21 по домену проходят
- [ ] 4.2 Change заархивирован (`openspec archive public-domain-origin`), `openspec validate --all --strict` зелёный
