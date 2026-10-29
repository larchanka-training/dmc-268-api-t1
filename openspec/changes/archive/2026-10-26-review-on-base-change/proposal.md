## Why

Security-аудит задачи #222 нашёл обход merge gate. Автор PR толкает свою ветку, в которой бэкдор уже лежит, открывает PR в неё - в диффе только безобидная правка, бот выставляет check run `success` на `head_sha`. Потом автор меняет целевую ветку PR на `develop`: GitHub шлёт `pull_request` `edited` с `changes.base`, приём его игнорирует, а check run на том же `head_sha` остаётся `success`. Обязательная проверка пройдена - бэкдор уходит в `develop` без ревью.

## What Changes

- `pull_request` `edited` с `changes.base` запускает ревью, как `synchronize`; `edited` без смены целевой ветки (заголовок, описание) по-прежнему игнорируется.
- Дублем считается прогон на тот же head-коммит **и** ту же базу. Прогон без записанной базы считается ревью любой базы.
- Активный прогон на старой базе при смене целевой ветки отменяется (`cancelled`) своим коммитом, и ставится новый прогон.
- Повтор упавших доставок переотправляет и `edited` - его отбор идёт по тем же действиям приёма.

## Capabilities

### Modified Capabilities

- `webhook-intake`: ADDED «Смена целевой ветки запускает ревью»; MODIFIED «Прочие действия pull request игнорируются», «Повторная доставка отвечает существующим прогоном», «Упавшие доставки повторяются».

## Impact

- Код: `app/domain/webhook.py`, `app/application/use_cases/handle_webhook.py`, докстринг `WebhookEvent` (`app/domain/entities.py`).
- Тесты: `tests/domain/test_webhook.py`, `tests/application/test_handle_webhook.py`, сквозной `tests/security/test_pr_manipulation_e2e.py`; фейк `webhook_payload("edited")`.
- Схема базы, контракт API и настройки не меняются. Событие `pull_request` у App уже подписано, `edited` приходит в нём же.
