## MODIFIED Requirements

### Requirement: Возврат на исходную страницу

`GET /api/v1/auth/{provider}/login` SHALL принимать необязательный параметр `redirect` и после успешного входа SHALL перенаправлять на него вместо `/app/`. Значение `redirect` SHALL приниматься, только если это относительный путь под `/app/` не длиннее 1000 байт в UTF-8; иное значение SHALL игнорироваться, и вход SHALL завершаться перенаправлением на `/app/`.

#### Scenario: Вход с глубокой ссылки

- **WHEN** вход начат с `redirect=/app/reviews/42` и завершился успешно
- **THEN** ответ callback — редирект на `/app/reviews/42`

#### Scenario: Попытка открытого редиректа

- **WHEN** вход начат с `redirect=https://evil.example/` или `redirect=//evil.example/app/`
- **THEN** после входа ответ — редирект на `/app/`

#### Scenario: Слишком длинный адрес возврата

- **WHEN** вход начат с `redirect` под `/app/` длиннее 1000 байт
- **THEN** после входа ответ — редирект на `/app/`, а cookie `oauth_state` остаётся в пределах, которые принимает браузер
