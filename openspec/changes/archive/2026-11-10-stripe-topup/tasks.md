## 1. Данные

- [x] 1.1 Сущность `BalanceCredit`: необязательный `stripe_checkout_session_id`; модель `BalanceCreditRow` и частичный уникальный индекс `uq_balance_credits_stripe_checkout_session_id`
- [x] 1.2 Миграция `0022`: колонка и индекс, откат; `upgrade → downgrade -1 → upgrade`
- [x] 1.3 Адаптер `SqlAlchemyBillingRepo.add_credit` — `INSERT … ON CONFLICT DO NOTHING`, порт `BillingRepo.add_credit` возвращает, записано ли; интеграционный тест повтора и гонки (`tests/db/test_stripe_credit.py`)

## 2. Зачисление

- [x] 2.1 Входной тип `CheckoutSession` (`app/application/use_cases/billing.py`): стабильные поля сессии Checkout; тесты правила без базы
- [x] 2.2 Use case `credit_stripe_checkout`: поиск пользователя по id сервиса, идемпотентная запись, строка лога; тесты с фейковым `UnitOfWork`
- [x] 2.3 Потолок `STRIPE_TOPUP_LIMIT_EUR` под блокировкой плательщика, `BillingRepo.stripe_total`; тесты потолка, повтора после него и гонки двух сессий на базе

## 3. HTTP

- [x] 3.1 `app/config.py`: `STRIPE_WEBHOOK_SECRET` и `STRIPE_API_KEY` (`SecretStr`, пусто — выключено); контейнер собирает `StripeClient` с тайм-аутом 5 с без своих повторов
- [x] 3.2 Коды `PAYMENTS_DISABLED` (`503`) и `PAYMENT_PROVIDER_UNAVAILABLE` (`502`), `WEBHOOK_SIGNATURE_INVALID` допускает `400`; словари `ERRORS_*`
- [x] 3.3 Роут `POST /api/v1/webhooks/stripe` (`app/api/stripe_webhook.py`) — адаптер SDK `stripe`: thin-уведомление, запрос сессии, подключён раньше `/webhooks/{provider}`; тесты подписи, выключенного режима, сбоя Stripe, правил зачисления, `Origin` и маршрута
- [x] 3.4 `GET /api/v1/auth/me` отдаёт `id`
- [x] 3.5 `uv run python scripts/export_openapi.py`

## 4. Развёртывание, наблюдаемость, документация

- [x] 4.1 Переменные OpenTofu `stripe_webhook_secret`, `stripe_api_key`, env только у `api` (`infra/app.tf`), `deploy.yml` передаёт `STRIPE_WEBHOOK_SECRET_DMC268_T1` и `STRIPE_API_KEY_DMC268_T1`; тест OpenTofu
- [x] 4.2 Алерт `StripeWebhookFailing` и его тесты promtool, строка в таблице алертов `infra/README.md`
- [x] 4.3 `.env.example`, `docker-compose.yml`, `infra/README.md`, runbook `docs/LOCAL_RUN.md` «Пополнение через Stripe», `docs/erd.md`, `Makefile`
