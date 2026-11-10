## ADDED Requirements

### Requirement: Приём вебхука Stripe

`POST /api/v1/webhooks/stripe` SHALL принимать thin-события Stripe без сессии и без `Origin` и SHALL проверять подпись `Stripe-Signature` по сырому телу секретом `STRIPE_WEBHOOK_SECRET` с допуском 300 с до разбора события. Подписи нет, она не совпала, устарела или тело не thin-событие — ответ SHALL быть `400 WEBHOOK_SIGNATURE_INVALID` без записей. Без `STRIPE_WEBHOOK_SECRET` или `STRIPE_API_KEY` ответ SHALL быть `503 PAYMENTS_DISABLED` без записей.

#### Scenario: Неверная подпись

- **WHEN** событие подписано другим секретом
- **THEN** ответ `400` с `code = WEBHOOK_SIGNATURE_INVALID`, запроса к Stripe нет, журнал зачислений не меняется

#### Scenario: Устаревшая подпись

- **WHEN** подпись верна, но её метка времени старше 300 с
- **THEN** ответ `400` с `code = WEBHOOK_SIGNATURE_INVALID`, журнал не меняется

#### Scenario: Приём не настроен

- **WHEN** `STRIPE_API_KEY` пуст, и приходит событие оплаты
- **THEN** ответ `503` с `code = PAYMENTS_DISABLED`, журнал не меняется

### Requirement: Зачисление оплаты Stripe

По `v1.checkout.session.completed` или `v1.checkout.session.async_payment_succeeded` сервис SHALL запросить сессию Checkout у Stripe и при `mode = payment`, `payment_status = paid` и `currency = eur` SHALL зачислить `amount_total / 100` € аккаунту из `client_reference_id` с `credited_by = stripe`. Иначе SHALL быть `200` без зачисления и строка лога с id; прочие события — `200` без запроса. Сбой запроса SHALL давать `502 PAYMENT_PROVIDER_UNAVAILABLE`, сбой базы — `5xx`.

#### Scenario: Оплата картой

- **WHEN** приходит `v1.checkout.session.completed`, а сессия у Stripe — `mode = payment`, `payment_status = paid`, `currency = eur`, `amount_total = 1000`, `client_reference_id` пользователя `alice`
- **THEN** ответ `200`, `alice` зачислено 10 € с `credited_by = stripe`

#### Scenario: Асинхронная оплата

- **WHEN** по `v1.checkout.session.completed` сессия была `unpaid`, а по `v1.checkout.session.async_payment_succeeded` той же сессии — `paid`
- **THEN** первое событие ничего не зачисляет, второе зачисляет сумму сессии

#### Scenario: Сессия без оплаты

- **WHEN** сессия события — `mode = setup` с `payment_status = no_payment_required`, или `amount_total = 0`
- **THEN** ответ `200`, зачисления нет

#### Scenario: Другая валюта

- **WHEN** оплаченная сессия в `usd`
- **THEN** ответ `200`, зачисления нет, в логе строка с id события и сессии

#### Scenario: Неизвестный пользователь

- **WHEN** `client_reference_id` сессии пуст или не id пользователя сервиса
- **THEN** ответ `200`, зачисления нет, в логе строка с id события и сессии

#### Scenario: Прочее событие

- **WHEN** приходит подписанное thin-событие `v1.checkout.session.expired`
- **THEN** ответ `200`, запроса к Stripe нет, журнал не меняется

#### Scenario: Stripe не отдал сессию

- **WHEN** запрос сессии упал по тайм-ауту или Stripe ответил ошибкой
- **THEN** ответ `502` с `code = PAYMENT_PROVIDER_UNAVAILABLE`, журнал не меняется, Stripe повторит доставку

### Requirement: Одно зачисление на сессию оплаты Stripe

Зачисление Stripe SHALL хранить id сессии Checkout, и на одну сессию SHALL приходиться не больше одного зачисления: при повторной доставке события, при двух событиях одной сессии и при одновременной обработке доставок. Повтор SHALL давать `200` без второго зачисления. Ручные зачисления id сессии SHALL NOT иметь.

#### Scenario: Повтор доставки

- **WHEN** одно и то же событие оплаты доставлено дважды
- **THEN** оба ответа `200`, зачисление одно

#### Scenario: Два события одной сессии

- **WHEN** после зачисления по `v1.checkout.session.completed` приходит `v1.checkout.session.async_payment_succeeded` той же сессии
- **THEN** ответ `200`, второго зачисления нет

#### Scenario: Одновременные доставки

- **WHEN** две доставки одной сессии обрабатываются одновременно
- **THEN** зачисление одно, обе доставки получают `200`

### Requirement: Алерт на отказы вебхука Stripe

Алерт `StripeWebhookFailing` SHALL гореть, когда за 6 часов больше половины ответов `POST /api/v1/webhooks/stripe` — 4xx или 5xx и таких ответов хотя бы два. Ответы других ручек SHALL NOT в него входить.

#### Scenario: Секрет не того назначения

- **WHEN** за два часа три доставки Stripe получили `400`, и ни одна не принята
- **THEN** горит `StripeWebhookFailing`

#### Scenario: Одиночный сбой

- **WHEN** за 6 часов одна доставка получила `503`, или две получили `502`, а четыре приняты
- **THEN** `StripeWebhookFailing` молчит

## MODIFIED Requirements

### Requirement: Ручное зачисление

Помимо пополнения через Stripe, администратор SHALL зачислять деньги на баланс
аккаунта по числовому id пользователя GitHub скриптом с суммой и заметкой. Логин SHALL NOT
определять получателя: GitHub отдаёт освободившийся логин другому человеку, а запись пользователя
сервиса обновляет логин только при входе. Скрипт SHALL отказывать без записи, если аккаунта с
таким id нет или сумма не число, и SHALL печатать логин аккаунта и его новый баланс.

#### Scenario: Зачисление по id GitHub

- **WHEN** администратор зачисляет аккаунту с id GitHub `583231` 5 € с заметкой «тест»
- **THEN** в журнале запись на 5 € с заметкой, скрипт печатает логин этого аккаунта и его баланс

#### Scenario: Логин сменил владельца

- **WHEN** у сервиса записан пользователь с логином `bob`, сменивший его на GitHub, и логин `bob` теперь у другого человека
- **THEN** зачисление по id нового владельца отказывает (он у нас не входил), а деньги не попадают прежнему `bob`

#### Scenario: Неизвестный id

- **WHEN** аккаунта с таким id GitHub среди пользователей сервиса нет
- **THEN** скрипт завершается ошибкой, журнал не меняется
