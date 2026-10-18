## Context

Пользовательской аутентификации в сервисе нет: ручки `/api/v1` открыты, единственная проверка — подпись вебхуков. С GitHub бэкенд работает как GitHub App: `GitHubVcsGateway` выпускает installation-токен на установку и ходит за диффом и комментариями от имени приложения (`docs/BACKEND_ARCHITECTURE.md`).

`PIPELINE_SPEC.md` §9.1 и `openapi.yaml` описывают предложение: `POST /auth/oauth/token` и `POST /auth/refresh`, access-токен в теле и в памяти вкладки, refresh-токен в httpOnly cookie. Кабинет ui#16 сделан под это предложение на моке.

Интерфейс и API отдаются с одного origin: nginx интерфейса проксирует `/api/` на API. Приложение живёт под `/app/`, страница входа — статичная `/login` (ui#24). Статус прогона приходит по SSE через нативный `EventSource` (ui#20).

## Goals / Non-Goals

**Goals:**

- Вход через GitHub, общий для всех вкладок браузера.
- Выход и отзыв сессии действуют сразу.
- Ни один токен не доступен JS страницы.
- SSE и обычные запросы авторизуются одинаково, без заголовков.

**Non-Goals:**

- Клиенты API на другом origin и мобильные приложения. Если появятся, им выдаются отдельные токены рядом с сессиями.
- Роли и права на репозитории: кто может регистрировать репозиторий и запускать ревью (api#41) — отдельный change поверх этого.
- Список установок GitHub App пользователя и подключение репозиториев.
- Хранение токенов пользователей у провайдеров.
- Вход через GitLab: репозитории GitLab сервис пока не подключает. Порт `OAuthProvider` и `{provider}` в путях оставляют место для его адаптера.

## Decisions

### Сессия в cookie вместо access- и refresh-токенов

| | access + refresh (§9.1) | сессия в cookie |
| --- | --- | --- |
| Токен в JS | access-токен в памяти вкладки | нет |
| Вкладки | у каждой свой access-токен | одна сессия на все |
| Выход | действующие access-токены живут до истечения | удаление строки, сразу везде |
| Ротация refresh | гонка вкладок; повторное использование отзывает сессию | не нужна |
| SSE | заголовок — нужна библиотека и обновление токена в потоке | нативный `EventSource` |
| CSRF | не подвержен | `SameSite=Lax` и проверка `Origin` |
| Хранение на бэкенде | refresh-токены, иначе нет отзыва | сессии |

Хранение нужно в обоих вариантах: без хранения refresh-токенов их не отозвать. Значит, выигрыш access-токена — проверка без обращения к базе — оплачивается сложностью на обеих сторонах. Проверка сессии — один запрос по уникальному индексу.

### Значение cookie и хранение

Значение cookie — 32 случайных байта (`secrets.token_urlsafe(32)`), без структуры и подписи. В `sessions` лежит SHA-256 от него: утечка таблицы не даёт действующих cookie. Поиск — по уникальному индексу на хэше.

Сессии хранятся в PostgreSQL, не в Redis. Поход в Redis — такой же сетевой запрос, а ручки `/api/v1` всё равно идут в базу за данными. Кэш перед базой пришлось бы сбрасывать при выходе, иначе выход перестаёт действовать сразу; in-process кэш со второй репликой ломает это совсем. Если проверка сессии станет заметна в профиле, сессия кладётся в `CacheStore` по `token_hash` с коротким TTL и сбросом в `logout` — без изменения контракта и схемы.

Срок жизни сессии — 30 дней от входа, без продления. Просроченная сессия при обращении считается недействительной и удаляется. Периодическая чистка — отдельная задача, когда таблица начнёт расти.

### Атрибуты cookie

`session=<значение>; HttpOnly; Secure; SameSite=Lax; Path=/; Max-Age=2592000`.

- `HttpOnly` — недоступна JS.
- `Path=/` — уходит и на `/api/`, и на `/login`: по ней nginx интерфейса уводит вошедшего со страницы входа в приложение.
- `SameSite=Lax`, а не `Strict`: со `Strict` переход по ссылке из комментария на GitHub открывал бы приложение без сессии. `Lax` не отправляет cookie с изменяющими запросами с чужих сайтов.

### OAuth на бэкенде

1. `GET /api/v1/auth/{provider}/login?redirect=…` генерирует `state`, кладёт его и проверенный `redirect` в cookie `oauth_state` (`HttpOnly; Secure; SameSite=Lax; Path=/api/v1/auth; Max-Age=600`) и отвечает `302` на страницу авторизации провайдера.
2. Провайдер возвращает на `GET /api/v1/auth/{provider}/callback?code=…&state=…`. `SameSite=Lax` пропускает `oauth_state` с этим переходом верхнего уровня.
3. Бэкенд сверяет `state` с cookie, обменивает `code` на токен пользователя секретом приложения и одним запросом получает профиль: `GET /user`.
4. Находит или создаёт пользователя по паре `provider` + `provider_user_id`, обновляет логин, имя и аватар.
5. Создаёт сессию, ставит cookie `session`, удаляет `oauth_state` и отвечает `302` на `redirect` или на `/app/`.

```mermaid
sequenceDiagram
    autonumber
    actor U as Браузер
    participant N as nginx интерфейса
    participant A as API
    participant G as GitHub
    participant DB as PostgreSQL

    U->>N: GET /app/reviews/42 (cookie session нет)
    N-->>U: 302 /login?redirect=/app/reviews/42
    U->>N: GET /login?redirect=/app/reviews/42
    N-->>U: статичная страница входа
    Note over U: скрипт дописывает redirect к ссылкам входа
    U->>A: GET /api/v1/auth/github/login?redirect=/app/reviews/42
    A->>A: redirect — путь под /app/, иначе /app/
    A-->>U: 302 на github.com/login/oauth/authorize?state=…<br/>Set-Cookie: oauth_state (state, redirect)
    U->>G: вход и согласие
    G-->>U: 302 на /api/v1/auth/github/callback?code=…&state=…
    U->>A: callback + cookie oauth_state
    A->>A: state из запроса = state из cookie, redirect проверен снова
    A->>G: обмен code на токен пользователя (секрет приложения)
    G-->>A: токен пользователя
    A->>G: GET /user
    G-->>A: id, login, avatar
    Note over A: токен пользователя выброшен
    A->>DB: upsert users по (github, id)<br/>insert sessions (хэш cookie, срок)
    A-->>U: 302 на /app/reviews/42<br/>Set-Cookie: session, удалить oauth_state
    U->>N: GET /app/reviews/42 (cookie session есть)
    N-->>U: HTML приложения
```

Токен пользователя после шага 3 не сохраняется. Ревью продолжает работать через installation-токены GitHub App.

Ошибка на любом шаге — `302` на `/login?error=<код>`: `state_mismatch`, `denied` (пользователь отказал у провайдера), `provider_unavailable`. Сессия не создаётся.

Для GitHub используется OAuth самого GitHub App (его `client_id` и `client_secret`), а не отдельное OAuth App: один реестр приложения, и пользователь видит одно приложение.

### Возврат на исходную страницу

nginx интерфейса уводит гостя с `/app/…` на `/login?redirect=<путь>`, страница входа дописывает `redirect` к ссылкам на `/api/v1/auth/{provider}/login`. Бэкенд принимает `redirect`, только если он начинается с `/app/` и не содержит `//`, `\` и схемы; иначе — `/app/`. Проверка — дважды: при начале входа и перед редиректом в callback, потому что cookie `oauth_state` приходит от клиента.

### Провайдеры

Провайдер один — GitHub: OAuth самого GitHub App, у него нет scope, приложение получает только профиль. Callback регистрируется в настройках приложения.

Провайдер — адаптер порта `OAuthProvider`, выбранный по `{provider}` из пути; неизвестный — `404`. Второй провайдер добавляется адаптером и настройками, без изменения контракта и схемы.

### Cookie по HTTP

`Secure` браузер не принимает по HTTP, кроме `localhost`. Стенд без `PUBLIC_HOST` отдаётся по HTTP, и вход на нём работать не будет — это осознанно: сессия по открытому каналу не передаётся. Локальная разработка на `localhost` работает. Флага, отключающего `Secure`, нет.

### Логи

Значение cookie сессии равносильно паролю. Ни приложение, ни uvicorn не пишут заголовки `Cookie` и `Set-Cookie`; токен провайдера и `code` не попадают в сообщения об ошибках.

### Какие ручки требуют сессию

Требуют: все ручки `/api/v1`, кроме `/webhooks/*` (у них своя проверка подписи), `/auth/{provider}/login`, `/auth/{provider}/callback` и `/health`. В том числе `GET /api/v1/reviews/{id}/events` (SSE).

Без сессии — `401` с `ApiError.code = UNAUTHENTICATED`. Если cookie `session` пришла, но недействительна, тот же ответ её удаляет (`Max-Age=0`). Так nginx интерфейса перестаёт считать пользователя вошедшим, и страница входа не уводит его обратно в приложение по кругу.

Проверка — зависимость FastAPI на уровне роутера, а не middleware: исключения видны в объявлении маршрутов.

Поток SSE зависимость проверяет один раз, при подключении, а дальше он живёт до конца прогона. Поэтому цикл потока, который раз в секунду читает прогон (api#56), раз в 5 секунд заодно перечитывает сессию по хэшу cookie — тот же запрос по уникальному индексу. Сессии нет или срок истёк — поток закрывается без события. `EventSource` переподключается, получает `401` и больше не пробует; кабинет уводит на `/login`.

Рабочие запросы, протухшая сессия и выход:

```mermaid
sequenceDiagram
    actor T1 as Вкладка 1
    actor T2 as Вкладка 2
    participant N as nginx интерфейса
    participant A as API
    participant DB as PostgreSQL

    T1->>A: GET /api/v1/reviews/42 (cookie session)
    A->>DB: сессия по хэшу cookie
    DB-->>A: user, expires_at
    A-->>T1: 200
    T2->>A: GET /api/v1/reviews/42/events (cookie session)
    A-->>T2: 200 text/event-stream

    T1->>A: POST /api/v1/auth/logout (cookie, Origin)
    A->>DB: delete sessions
    A-->>T1: 204, Set-Cookie: session удалена
    Note over A,T2: поток T2 перечитывает сессию — её нет, поток закрыт
    T2->>A: следующий запрос (та же cookie)
    A->>DB: сессии нет
    A-->>T2: 401 UNAUTHENTICATED, Set-Cookie: session удалена
    T2->>N: GET /login (cookie уже нет)
    N-->>T2: страница входа, а не редирект в /app/
```

### CSRF

`SameSite=Lax` не отправляет cookie с `POST` с чужого сайта, но защита не должна держаться на одном атрибуте. Изменяющие запросы (`POST`, `PUT`, `PATCH`, `DELETE`) к ручкам с сессией сверяют заголовок `Origin` с `PUBLIC_ORIGIN`. Нет `Origin` или он чужой — `403` с `ApiError.code = ORIGIN_FORBIDDEN`. Вебхуки под проверку не попадают: у них нет cookie и своя подпись.

### Данные

```text
users
  id                uuid PK
  provider          provider NOT NULL        -- enum из 0001: github, gitlab
  provider_user_id  text NOT NULL
  login             text NOT NULL
  name              text NULL
  avatar_url        text NULL
  created_at        timestamptz NOT NULL
  updated_at        timestamptz NOT NULL
  UNIQUE (provider, provider_user_id)

sessions
  id                uuid PK
  token_hash        bytea NOT NULL UNIQUE
  user_id           uuid NOT NULL REFERENCES users ON DELETE CASCADE
  created_at        timestamptz NOT NULL
  expires_at        timestamptz NOT NULL
```

`provider` в ключе — чтобы второй провайдер не потребовал миграции; тип — тот же нативный enum `provider`, что у `repositories`, в нём уже есть `gitlab`. `provider_user_id` — строка: тип идентификатора у провайдеров разный, а сравнивать их между собой не нужно. Миграция одна, `downgrade` удаляет обе таблицы.

### Слои

- `app/domain`: `User`, `Session`, правило срока жизни.
- `app/application`: порт `OAuthProvider` (`authorize_url`, `exchange_code` → профиль), порт репозиториев пользователей и сессий; use cases `start_login`, `complete_login`, `authenticate`, `logout`.
- `app/infrastructure`: адаптер GitHub на `httpx`, репозитории на SQLAlchemy.
- `app/api`: роутер `/auth`, зависимость `current_user`, проверка `Origin`.

## Risks / Trade-offs

- **CSRF держится на `SameSite` и `Origin`.** → Обе защиты обязательны и покрыты тестами; изменяющих ручек пока мало.
- **Сессия на 30 дней без продления** выбрасывает активного пользователя раз в месяц. → Продление добавляется без изменения контракта, если начнёт мешать.
- **Утечка cookie** даёт доступ до выхода или истечения срока. → `HttpOnly` и `Secure`; выход удаляет сессию сразу.
- **Мок ui#16 переписывается.** → Реализаций под §9.1 нет, переписывается только мок.

## Open Questions

- Нужен ли выход из всех сессий пользователя («выйти на всех устройствах»): в схеме есть, ручка — когда понадобится.
- Кто получает доступ к прогонам и репозиториям: пока любой вошедший видит всё, права — в api#41.
