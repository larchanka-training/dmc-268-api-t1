## Context

`deploy.yml` (job деплоя, env шага OpenTofu) ставит `TF_VAR_public_origin: https://${{ vars.VPS_DMC268_IP_T1 }}`. Та же переменная IP — адрес SSH (`host:`/`HOST:`). Вход на стенде — nginx фронта с `ngx_http_acme_module`, сертификат в томе `dmc268-ui-acme` (репозиторий интерфейса). Вебхук — `POST /api/v1/webhooks/{provider}` (`app/api/webhooks.py`), для GitHub — `/api/v1/webhooks/github`.

## Decisions

- **D1. Отдельная переменная `PUBLIC_ORIGIN`, а не замена `VPS_DMC268_IP_T1`; без неё — адрес по IP.** IP нужен для SSH, и смешивать адрес сервера с адресом сервиса — то, из-за чего origin пришлось бы собирать строкой. Пока `PUBLIC_ORIGIN` не задана, `deploy.yml` подставляет `https://` + `VPS_DMC268_IP_T1` (`vars.PUBLIC_ORIGIN || format('https://{0}', vars.VPS_DMC268_IP_T1)`) — так же, как фронт: PR можно мёржить до выбора домена, стенд продолжает работать по IP, а не падает на деплое. Отдельного шага проверки конфигурации в деплое нет, проверки две: формат — `validation` переменной `public_origin` в `infra/variables.tf` (`https://<хост>` строчными, домен или IPv4, без порта, слеша и точки на конце, пути — значение ровно `https://` + `PUBLIC_HOST` фронта), падает на `plan` до изменений на сервере; пустое значение (не заданы обе переменные) — защита в начале `.github/scripts/deploy/infra.sh`, до первого `plan`. Переменная пустую строку пропускает: локальный apply живёт без адреса.
- **D2. Порядок выкатки.** (1) DNS A-запись на `62.169.24.237`, проверка `dig`; (2) фронт с доменом на staging Let's Encrypt (`acme_directory`): лимит — 5 одинаковых сертификатов в неделю; (3) бэкенд с `PUBLIC_ORIGIN`; (4) фронт на боевой Let's Encrypt; (5) админ App меняет callback, webhook URL и Setup URL (`/api/v1/auth/github/setup`, «Redirect on update» включён). Том `dmc268-ui-acme` не трогаем. Окно `403` между (2) и (3) — минуты, изменяющих запросов на стенде в это время мало. С (2) до (4) горит критический `PublicEndpointDown` (blackbox `https_public` по `public_origin` проверяет цепочку, staging-сертификату не доверяет): перед (2) — тишина в Alertmanager (`amtool silence add alertname=PublicEndpointDown --duration=2h` в `dmc268-alertmanager`), после (4) — снять. Команды — `infra/README.md`, «Публичный адрес стенда».
- **D3. Фронт и бэкенд — разные PR в своих репозиториях**, связанные ui#41 и этим change; описание PR ссылается на парный.

## Risks / Trade-offs

- [Домен не выбран] → код смёржен, ручные задачи ждут; без `PUBLIC_ORIGIN` деплой берёт адрес по IP, текущий shortlived-сертификат продлевается сам.
- [DuckDNS недоступен или потерян аккаунт] → свой домен убирает зависимость. Решение — за командой.
- [Старые ссылки по IP] → nginx фронта может отвечать `301` с IP на домен; это решается в ui#41.

## Open Questions

- Какой домен: свой или поддомен DuckDNS? Блокирует выкатку, но не код — меняется только значение `PUBLIC_ORIGIN`.
