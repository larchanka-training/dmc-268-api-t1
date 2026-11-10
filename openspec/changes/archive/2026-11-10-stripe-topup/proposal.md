## Why

Баланс сейчас пополняет только администратор (`make credit-balance` в контейнере воркера). Пользователь должен пополнять его сам из кабинета через Stripe (larchanka-training/dmc-268-api-t1#411, бэкенд — #412). Ссылку оплаты (Stripe Payment Link) настраивают в Stripe Dashboard, сессию оплаты создаёт она сама; бэкенду остаётся принять событие об оплате и зачислить деньги.

## What Changes

- `POST /api/v1/webhooks/stripe` принимает thin-события Stripe (`v1.checkout.session.completed`, `v1.checkout.session.async_payment_succeeded`): подпись `Stripe-Signature` по сырому телу (SDK `stripe`, допуск 300 с), неверная, устаревшая или отсутствующая — `400 WEBHOOK_SIGNATURE_INVALID`; без `STRIPE_WEBHOOK_SECRET` или `STRIPE_API_KEY` — `503 PAYMENTS_DISABLED`.
- По событию оплаты сервис запрашивает сессию Checkout у Stripe restricted-ключом (тайм-аут 5 с, сбой — `502 PAYMENT_PROVIDER_UNAVAILABLE`) и зачисляет `amount_total / 100` € аккаунту из `client_reference_id`, если `mode = payment`, `payment_status = paid`, валюта `eur`. Иначе — `200` без зачисления и строка лога.
- Идемпотентность: id сессии Checkout в `balance_credits.stripe_checkout_session_id` с частичным уникальным индексом (миграция `0022`), вставка `ON CONFLICT DO NOTHING`.
- `GET /api/v1/auth/me` отдаёт `id` пользователя — его ссылка оплаты передаёт в параметре `client_reference_id`.
- Коды ошибок `PAYMENTS_DISABLED` (`503`) и `PAYMENT_PROVIDER_UNAVAILABLE` (`502`); `WEBHOOK_SIGNATURE_INVALID` допускает `400` (Stripe) наряду с `401` (GitHub).
- Алерт `StripeWebhookFailing`: Stripe о падениях нашего эндпоинта не сообщает.
- Настройки `STRIPE_WEBHOOK_SECRET` и `STRIPE_API_KEY`; деплой передаёт секреты `STRIPE_WEBHOOK_SECRET_DMC268_T1` и `STRIPE_API_KEY_DMC268_T1` только контейнеру `api`.

## Capabilities

### New Capabilities

### Modified Capabilities

- `review-billing`: пополнение баланса через Stripe и алерт его вебхука; ручное зачисление больше не единственный путь.
- `user-auth`: `GET /api/v1/auth/me` отдаёт `id` пользователя.

## Impact

- Код: `app/api/stripe_webhook.py`, `app/application/use_cases/billing.py`, репозиторий и модель `balance_credits`, `app/api/problems.py`, `app/api/errors.py`, `app/api/auth.py`, `app/config.py`, `app/infrastructure/container.py`.
- Схема: колонка `balance_credits.stripe_checkout_session_id` и индекс, ревизия `0022`.
- Зависимость `stripe~=16.1`.
- Развёртывание: переменные OpenTofu `stripe_webhook_secret` и `stripe_api_key`, env `api`, `deploy.yml`. Без секретов деплой зелёный, приём Stripe выключен.
- Наблюдаемость: `infra/observability/alerts.yml`.
- Контракт: `docs/openapi/openapi.json`. Runbook: `docs/LOCAL_RUN.md`, «Пополнение через Stripe».
