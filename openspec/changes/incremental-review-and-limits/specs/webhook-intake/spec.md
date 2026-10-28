# Spec Delta

## MODIFIED Requirements

### Requirement: Действия opened и synchronize запускают ревью

Система SHALL запускать прогон ревью по событиям `pull_request` с действиями `opened` и `synchronize`. Для них SHALL создаваться или обновляться запись запроса на изменение — при `synchronize` head-коммит существующей записи сдвигается на новый — и создаваться прогон ревью в состоянии `queued` с источником прогона `webhook`, после чего задача ревью SHALL ставиться в очередь. Успешный ответ — HTTP 202. Исключения задают лимиты спеки `review-limits` (`reject`, `defer`) и вытеснение прогона прежнего head по спеке `incremental-review`.

#### Scenario: Открытие PR запускает ревью

- **WHEN** приходит `pull_request` с действием `opened` и валидной подписью для зарегистрированного репозитория
- **THEN** ответ — HTTP 202, существует запись запроса на изменение и прогон ревью в состоянии `queued` с источником `webhook`, и в очереди появляется задача ревью

#### Scenario: Новый коммит в PR запускает новый прогон

- **WHEN** приходит `pull_request` с действием `synchronize` с валидной подписью для зарегистрированного репозитория
- **THEN** ответ — HTTP 202, head-коммит существующего запроса на изменение сдвигается на новый, создаётся новый прогон ревью в состоянии `queued`, и задача ставится в очередь

#### Scenario: Новый коммит при активном прогоне прежнего head

- **WHEN** приходит `synchronize` с новым head, а прогон прежнего head ещё в работе
- **THEN** ответ — HTTP 202, создаётся прогон нового head, прогон прежнего head помечается вытесненным и переходит в `cancelled` с причиной `superseded` на ближайшей проверке воркера

#### Scenario: Отложенный прогон

- **WHEN** приходит `synchronize`, а лимит прогонов PR за окно исчерпан
- **THEN** ответ — HTTP 202, прогон создан в `queued` с причиной отсрочки и в очередь пока не поставлен

### Requirement: Тело 202 приёма вебхука — модель схемы

Ответ `202` приёма вебхука SHALL быть одной из моделей схемы: `ReviewJob` со всеми полями — поле без значения приходит как `null`, время — в UTC с `Z`, — `{"status": "ignored"}` или `{"status": "acknowledged"}` для `pull_request_review_thread`. Ответ на `pull_request` SHALL дополнительно нести поле `intake_outcome`: `enqueued`, `deferred` или `duplicate` в `ReviewJob`; `stale_event`, `rate_limited`, `retry_budget_exhausted` или `unsupported_action` в `{"status": "ignored"}`. Остальные поля моделей не меняются.

#### Scenario: Новый прогон

- **WHEN** подписанный `pull_request` `opened` создал прогон
- **THEN** тело `202` проходит модель `ReviewJob`, `model`, `tokens_used`, `duration_seconds`, `failed_stage` и `error` — `null`, `created_at` оканчивается на `Z`, `intake_outcome` — `enqueued`

#### Scenario: Повторная доставка того же webhook

- **WHEN** тот же `X-GitHub-Delivery` для коммита с активным или завершённым не-`failed` прогоном доставлен повторно
- **THEN** ответ — HTTP 202 с проекцией существующего прогона и `intake_outcome` `duplicate`, новых записей и задач нет

#### Scenario: Отказ без прогона

- **WHEN** доставка отклонена лимитом частоты инсталляции
- **THEN** тело `202` — `{"status": "ignored", "intake_outcome": "rate_limited"}`

## ADDED Requirements

### Requirement: Устаревшая доставка не откатывает head

Доставка `synchronize`, у которой время обновления PR в payload раньше сохранённого времени
последнего сдвига head, SHALL NOT менять head запроса на изменение и SHALL NOT создавать
прогон. Ответ SHALL быть HTTP 202 с исходом `stale_event`.

#### Scenario: Повторная доставка старого push после нового

- **WHEN** после доставки push коммита B повторно доставляется вебхук push коммита A, сделанного раньше
- **THEN** head остаётся B, прогон для A не создаётся, ответ — 202 с исходом `stale_event`
