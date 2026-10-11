## 1. Контракт

- [ ] 1.1 `docs/openapi/openapi.yaml`: убрать `/auth/oauth/token`, `/auth/refresh`, `OAuthTokenRequest`, `AuthSession`, коды `AUTH_CODE_INVALID` и `AUTH_REFRESH_INVALID`; добавить `/auth/{provider}/login`, `/auth/{provider}/callback`, `/auth/me`, `/auth/logout`, cookie-схему безопасности, коды `UNAUTHENTICATED` и `ORIGIN_FORBIDDEN`
- [ ] 1.2 `docs/pipeline/PIPELINE_SPEC.md` §9.1: заменить предложение на ссылку на спеку `user-auth`
- [ ] 1.3 `openspec/config.yaml`: «JWT authorization» → сессия в httpOnly cookie

## 2. Данные

- [ ] 2.1 Модели и миграция Alembic: `users`, `sessions`; `downgrade` удаляет обе; `alembic heads` — одна ревизия
- [ ] 2.2 Репозитории пользователей и сессий на SQLAlchemy
- [ ] 2.3 `docs/erd.md`: таблицы `users` и `sessions`, связь `sessions.user_id` → `users.id`, хэш вместо значения cookie

## 3. Домен и use cases

- [ ] 3.1 `User`, `Session`, срок жизни сессии 30 дней
- [ ] 3.2 Порт `OAuthProvider`: адрес авторизации, обмен кода на профиль
- [ ] 3.3 Use cases `start_login`, `complete_login`, `authenticate`, `logout`

## 4. Провайдеры

- [ ] 4.1 GitHub: OAuth GitHub App, `GET /user`
- [ ] 4.2 GitLab: OAuth-приложение, `GET /api/v4/user`
- [ ] 4.3 Настройки: `GITHUB_APP_CLIENT_ID`, `GITHUB_APP_CLIENT_SECRET`, `GITLAB_OAUTH_CLIENT_ID`, `GITLAB_OAUTH_CLIENT_SECRET`, `PUBLIC_ORIGIN`; секреты — `SecretStr`, в логи не попадают

## 5. API

- [ ] 5.1 Роутер `/auth`: `login`, `callback`, `me`, `logout`
- [ ] 5.2 Зависимость `current_user` на роутерах REST и SSE; `401` удаляет недействительную cookie
- [ ] 5.3 Проверка `Origin` для изменяющих запросов с сессией; вебхуки вне её

## 6. Тесты

- [ ] 6.1 Callback: первый и повторный вход, `state` не совпал, отказ, провайдер недоступен — на фейковом `OAuthProvider`
- [ ] 6.2 Сессия: в базе только хэш, истёкшая — `401` и удаление cookie, выход прекращает доступ
- [ ] 6.3 Ручки чтения без сессии — `401`; вебхуки работают без сессии
- [ ] 6.4 `Origin`: чужой и отсутствующий — `403`, свой — проходит
- [ ] 6.5 Токен провайдера не попадает в базу и логи

## 7. Стенд

- [ ] 7.1 Callback зарегистрирован у GitHub App и OAuth-приложения GitLab, секреты — в окружении стенда
- [ ] 7.2 Вход через GitHub на стенде: cookie `session` поставлена, `/login` уводит в `/app/`
