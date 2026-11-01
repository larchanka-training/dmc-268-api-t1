# Локальный запуск против настоящего GitHub

Весь бэкенд на своей машине — API, воркер, PostgreSQL, RabbitMQ и Ollama — с настоящей GitHub
App: PR в тестовом репозитории получает ревью и check run `dmc-268-ai-review` от локального
воркера. Песочница (`make sandbox`, [`tests/sandbox/README.md`](../tests/sandbox/README.md))
проверяет только воркер на мок-PR; здесь — весь путь вебхук → API → очередь → воркер → GitHub.

Нужны: `uv`, Docker (Docker Desktop или любой другой с `docker compose`), `ollama`, `openssl`,
`curl` и [`cloudflared`](https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/downloads/)
для туннеля (`brew install cloudflared`). Модель по умолчанию — `qwen2.5-coder:7b`, около 4,7 ГБ
при первом запуске; 1.5b песочницы для ревью настоящих PR слаба.

```bash
make run-local    # проверить и поднять всё; чего не хватает - перечислит с подсказкой
make stop-local   # остановить API, воркер и туннель
```

`make run-local` идёт по шагам и на каждом либо поднимает нужное сам, либо говорит, как исправить:

| Шаг | Сам | Подскажет |
|---|---|---|
| Инструменты | — | нет `uv`, `docker`, `ollama`, `curl`, `openssl`; нет `cloudflared` — только предупреждение |
| Docker | запустит Docker Desktop на macOS и дождётся демона | Docker не отвечает |
| `.env` | создаст из `.env.example`, сгенерирует `USER_TOKEN_ENCRYPTION_KEY` | какие значения GitHub App не заданы (шаг 1 ниже) |
| Ollama и модель | `ollama serve` в фоне, `ollama pull` модели | — |
| База, брокер, миграции | `docker compose up` PostgreSQL и RabbitMQ, `alembic upgrade head` | — |
| API, воркер, туннель | в фоне, логи в `.local/` | упавший процесс — с хвостом его лога |

Чего-то не хватает — исправьте отмеченное ✗ и запустите `make run-local` снова: сделанное
повторно не делается, заполненный `.env` не перетирается, секреты не печатаются.

## 1. Своя dev GitHub App

Берите **свою** App, а не командную: у App один Webhook URL, и командная App, перенаправленная
на туннель ноутбука, перестаёт слать вебхуки стенду — ревью на стенде молча встают у всей
команды. Ключ командной App в `.env` не кладите и без смены URL: локальный воркер будет писать
ревью и check runs в подключённые к ней репозитории от её имени, а переотправка доставок по её
JWT затронет доставки стенда. В `.env` — только своя App. Своя создаётся за пару минут: GitHub →
Settings → Developer settings → GitHub Apps → New GitHub App.

- **Permissions**: Pull requests — Read and write, Contents — Read-only, Checks — Read and write,
  Metadata — Read-only.
- **Subscribe to events**: Pull request, Pull request review thread.
- **Callback URL**: `http://localhost:8000/api/v1/auth/github/callback` (`PUBLIC_ORIGIN` из
  `.env.example` + путь входа). С кабинетом на dev-сервере — `http://localhost:3000/…` и
  `PUBLIC_ORIGIN=http://localhost:3000`.
- **Webhook**: Active, secret — любой свой. URL пока любой: его выдаст `make run-local`.
- Сгенерируйте client secret и private key, **установите** App на тестовый репозиторий, где у
  вас admin или maintain.

В `.env` своим редактором: `GITHUB_APP_ID`, `GITHUB_APP_CLIENT_ID`, `GITHUB_APP_CLIENT_SECRET`,
`GITHUB_WEBHOOK_SECRET` (вместо `local-dev-secret`) и `GITHUB_APP_PRIVATE_KEY` — PEM целиком,
в двойных кавычках, с переносами строк:

```
GITHUB_APP_PRIVATE_KEY="-----BEGIN RSA PRIVATE KEY-----
MIIE...
-----END RSA PRIVATE KEY-----"
```

## 2. Запуск и туннель

Пока проверка окружения находит ✗, дальше неё `make run-local` не идёт. Процессы работают в фоне,
логи и PID-файлы — в `.local/`; `make stop-local` гасит только то, что запустил `run-local`, база,
брокер и Ollama остаются (`make infra-down` — остановить контейнеры). Модель — `MODEL=…`,
иначе `OLLAMA_MODEL` из `.env`, иначе `qwen2.5-coder:7b`. Адрес и модель Ollama воркер получает
переменными окружения, `.env` для этого не правится: тот же файл читает `docker compose`.

Туннель нужен, потому что ревью запускает только вебхук: GitHub шлёт `POST` на Webhook URL App,
а до `localhost` он не достаёт. `cloudflared` даёт временный публичный адрес и передаёт тело
запроса как есть — подпись `X-Hub-Signature-256` сходится. В итоге `make run-local` печатает
строку «Webhook URL для настроек GitHub App» — вставьте её в App и сохраните. Адрес новый при
каждом запуске: после `stop-local` / `run-local` обновите его снова. Свой туннель (ngrok) —
`make run-local TUNNEL=0` и `ngrok http 8000`. Пока туннель поднят, в интернет открыт весь
локальный API (`/docs`, `/api/v1/auth/github/login` и остальное), а не только вебхук, — держите
в этом экземпляре только тестовые данные.

## 3. Вход, репозиторий, PR

1. Откройте `http://localhost:8000/api/v1/auth/github/login` и разрешите App доступ. Ошибка
   входа приходит редиректом на `/login?error=<код>`; `provider_unavailable` — не задан
   client id/secret или ключ шифрования.
2. Подключите тестовый репозиторий (вебхуки незарегистрированных репозиториев сервис
   игнорирует, см. [README](../README.md#регистрация-репозитория)). Значение cookie `session` — из devtools браузера:

   ```bash
   curl -s -b "session=$SESSION" localhost:8000/api/v1/repositories    # строка с can_connect: true и connected: false
   curl -s -b "session=$SESSION" -H 'Origin: http://localhost:8000' -H 'Content-Type: application/json' \
     -d '{"provider": "github", "provider_id": "<provider_id из списка>"}' localhost:8000/api/v1/repositories
   ```

   Без `Origin`, равного `PUBLIC_ORIGIN`, ответ —
   `403 ORIGIN_FORBIDDEN`.
3. Откройте PR в тестовом репозитории (или запушьте в открытый) с явной проблемой — например,
   SQL, склеенный из ввода пользователя.

Ожидаемо: в App → Advanced → Recent deliveries доставка `pull_request` с ответом `202`; в логе
воркера прогон проходит `fetching_diff → building_context → analysing → verifying → publishing`
и заканчивается `completed`; в PR — ревью от `<app-slug>[bot]` и check run `dmc-268-ai-review`;
прогон виден в `GET /api/v1/reviews` с той же cookie.

## Если не работает

| Симптом | Причина | Что делать |
|---|---|---|
| Доставка — timeout или 502 от туннеля | туннель упал или адрес устарел | `tail -F .local/*.log`, новый URL — в App |
| Доставка — `401` | `GITHUB_WEBHOOK_SECRET` не совпадает с секретом App | поправить `.env`, `make stop-local && make run-local` |
| `202`, а прогона нет | репозиторий не подключён или выключен — или PR из форка от автора не `OWNER`, `MEMBER` или `COLLABORATOR` | шаг 3.2; для форка — `docs/GITHUB_APP.md`, «Модель и деньги» |
| Вебхук — `502` | неверный `GITHUB_APP_ID` или private key | причина — в `tail -F .local/api.log`; проверить значения в `.env`, `make stop-local && make run-local` |
| GitHub: redirect_uri mismatch | Callback URL ≠ `PUBLIC_ORIGIN` + `/api/v1/auth/github/callback` | поправить Callback URL App |
| `verifying` — `LLM_INVALID_OUTPUT` | модель мала или сбивается с формата | модель 7b и больше |
| Ревью без правил, `rules.status = unavailable` | у App нет Contents: read | права App |
| Нет check run | у App нет Checks: write | права App |
| «порт 8000 уже занят» | работает `docker compose up` или прошлый `run-local` | `docker compose stop api worker` или `make stop-local` |
