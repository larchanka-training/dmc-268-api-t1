## Why

Неудачный callback входа всегда заканчивается редиректом на `/login?error=…`, а в логе API пусто. Адаптер GitHub собирает точную причину без секретов — `incorrect_client_credentials`, `redirect_uri_mismatch`, ответ `401` на профиль, — но `complete_login` её глотает. На стенде неверный секрет или незарегистрированный callback выглядят одинаково, и причину не найти.

## What Changes

- `complete_login` пишет `warning` с причиной для каждой неудачи, кроме отказа пользователя (`access_denied`): нет cookie `oauth_state`, `state` не совпал, провайдер вернул `error` без `code`, обмен кода не удался.
- В лог не попадают `state`, `code` и секреты; `error` из запроса — через `%r` и обрезанным до 100 символов, чтобы клиент не подделал строку лога.

## Impact

- `app/application/use_cases/auth.py`, `tests/application/test_auth.py`. Ответы API не меняются.
