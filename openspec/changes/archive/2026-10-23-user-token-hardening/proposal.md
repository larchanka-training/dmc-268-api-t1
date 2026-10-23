# Proposal

## Why

Независимое ревью безопасности PR #111 (change `repositories-via-user-token`, задача #107) блокеров не нашло, но указало на крайние случаи, где пользователь получает лишний `401` или `502`, и на неограниченные ожидания:

- `401` от GitHub на старый access token сразу после параллельного refresh закрывал сессию зря;
- временный сбой refresh давал `502`, хотя access token ещё действовал (обновляем за 5 минут до истечения);
- `403` на репозиториях одной установки (SSO организации не авторизован) ронял весь список в `502`; `403` лимита не отличался от `403` доступа;
- пагинация без верхней границы, ожидание чужой блокировки без предела, таймаут HTTP 10 секунд на фазу описан как общий;
- открытый токен в локальной переменной `headers`;
- выход не отзывал токен у GitHub.

## What Changes

- `401` на токен — перечитать токен; сменился — повторить вызов один раз, иначе закрыть сессию.
- Сбой обмена refresh token или чужая блокировка дольше 15 секунд при ещё действующем access token — используется прежний.
- Список: `403` (не лимит) на репозиториях установки — установка пропускается, как при `404`; `403` с `x-ratelimit-remaining: 0` или `retry-after` — `502`; пагинация до `total_count` и не больше 50 страниц.
- Транзакция refresh ждёт чужую блокировку не дольше 15 секунд (`SET LOCAL lock_timeout`); HTTP к GitHub от имени пользователя — `httpx.Timeout(10, connect=5)`.
- Токен в заголовок — через `httpx.Auth`.
- Выход отзывает токен у GitHub (`DELETE /applications/{client_id}/token`) после ответа, best-effort.

## Capabilities

### Modified Capabilities

- `review-rest-api`: «Список репозиториев, доступных для подключения», «Истёкший токен пользователя обновляется», «Непригодный токен пользователя закрывает сессию».
- `user-auth`: «Выход» — отзыв токена у провайдера.

## Impact

`app/application/use_cases/user_token.py`, `app/application/use_cases/auth.py` (`logout` отдаёт токен для отзыва), `app/api/auth.py`, порт `OAuthProvider.revoke`, `app/infrastructure/oauth/github.py`, `app/infrastructure/oauth/bearer.py`, `app/infrastructure/vcs/github_installations.py`, `app/infrastructure/db/repositories.py`, `app/infrastructure/container.py`. Схема ответов не меняется.
