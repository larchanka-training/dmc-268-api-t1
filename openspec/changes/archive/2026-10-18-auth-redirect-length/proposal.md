## Why

`redirect` хранится в cookie `oauth_state` в процент-кодировке, а `safe_redirect` пропускал путь любой длины. Путь из 700 кириллических или иных не-ASCII символов давал cookie больше 4096 байт: браузер её отбрасывает, и вход по такой ссылке всегда кончался `state_mismatch`. Такую ссылку может прислать кто угодно.

## What Changes

- `safe_redirect` заменяет на `/app/` адрес длиннее 1000 байт в UTF-8: в процент-кодировке это не больше 3000 байт, cookie укладывается в предел браузера.
- В OpenAPI самого FastAPI у `GET /auth/me` появляется ответ `401`, как в `docs/openapi/openapi.yaml`.

## Impact

- `app/domain/login.py`, `app/api/auth.py`, `tests/domain/test_login.py`, спека `user-auth`.
