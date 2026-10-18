## 1. Контракт

- [x] 1.1 `docs/openapi/openapi.yaml`: убрать `/auth/oauth/token`, `/auth/refresh`, `OAuthTokenRequest`, `AuthSession`, коды `AUTH_CODE_INVALID` и `AUTH_REFRESH_INVALID`; добавить `/auth/{provider}/login`, `/auth/{provider}/callback`, `/auth/me`, `/auth/logout`, cookie-схему безопасности, коды `UNAUTHENTICATED` и `ORIGIN_FORBIDDEN`
- [x] 1.2 `docs/pipeline/PIPELINE_SPEC.md`: §9.1 — заменить предложение на ссылку на спеку `user-auth`; §9 — в закрытом списке кодов `ApiError` заменить `AUTH_CODE_INVALID` и `AUTH_REFRESH_INVALID` на `UNAUTHENTICATED` и `ORIGIN_FORBIDDEN`
- [x] 1.3 `openspec/config.yaml`: «JWT authorization» → сессия в httpOnly cookie
- [x] 1.4 `docs/BACKEND_ARCHITECTURE.md`: порт `OAuthProvider`, адаптер GitHub, зависимость `current_user`, проверка `Origin`
- [x] 1.5 `docs/SYSTEM_DESIGN.md`: вход пользователя через OAuth на бэкенде и сессия в cookie — рядом с GitHub App
- [x] 1.6 `README.md` и `.env.example`: новые переменные окружения

## 2. Данные

- [x] 2.1 Модели и миграция Alembic: `users`, `sessions`; `downgrade` удаляет обе; `alembic heads` — одна ревизия
- [x] 2.2 Репозитории пользователей и сессий на SQLAlchemy
- [x] 2.3 `docs/erd.md`: таблицы `users` и `sessions`, связь `sessions.user_id` → `users.id`, хэш вместо значения cookie

## 3. Домен и use cases

- [x] 3.1 `User`, `Session`, срок жизни сессии 30 дней
- [x] 3.2 Порт `OAuthProvider`: адрес авторизации, обмен кода на профиль
- [x] 3.3 Use cases `start_login`, `complete_login`, `authenticate`, `logout`

## 4. Провайдеры

- [x] 4.1 GitHub: OAuth GitHub App, `GET /user`
- [x] 4.2 Настройки: `GITHUB_APP_CLIENT_ID`, `GITHUB_APP_CLIENT_SECRET`, `PUBLIC_ORIGIN`; секреты — `SecretStr`, в логи не попадают

## 5. API

- [x] 5.1 Роутер `/auth`: `login`, `callback`, `me`, `logout`
- [x] 5.2 Зависимость `current_user` на роутерах REST и SSE; `401` удаляет недействительную cookie
- [x] 5.3 Проверка `Origin` для изменяющих запросов с сессией; вебхуки вне её
- [x] 5.4 `redirect`: только относительный путь под `/app/`, проверка при начале входа и в callback
- [x] 5.5 Логи: ни `Cookie`, ни `Set-Cookie`, ни токен провайдера, ни `code`
- [x] 5.6 Поток SSE перечитывает сессию в своём цикле не реже раза в 5 секунд и закрывается, когда её нет или срок истёк

## 6. Тесты

- [x] 6.1 Callback: первый и повторный вход, `state` не совпал, отказ, провайдер недоступен — на фейковом `OAuthProvider`
- [x] 6.2 Сессия: в базе только хэш, истёкшая — `401` и удаление cookie, выход прекращает доступ, в том числе к открытому потоку SSE
- [x] 6.3 Ручки чтения без сессии — `401`; вебхуки работают без сессии
- [x] 6.4 `Origin`: чужой и отсутствующий — `403`, свой — проходит
- [x] 6.5 Токен провайдера и cookie сессии не попадают в базу и логи
- [x] 6.6 `redirect`: глубокая ссылка возвращается, внешний адрес и `//host` — нет
- [x] 6.7 Существующие тесты REST (`test_repositories_api.py`, `test_reviews_api.py`, `test_pull_requests_api.py`): фикстура вошедшего пользователя
- [x] 6.8 `docs/testing/TEST_PLAN.md`: кейсы входа, сессии, `Origin` и `redirect`

## 7. Стенд

Реквизиты GitHub App на стенде (callback, client secret) и проверка входа там — в change `deploy-github-app-credentials`: они ждут администратора App и не блокируют код.

- [x] 7.2 `infra/variables.tf` и `infra/main.tf`: новые переменные в окружение контейнера API; секреты — в GitHub Actions, как `GITHUB_APP_PRIVATE_KEY`
