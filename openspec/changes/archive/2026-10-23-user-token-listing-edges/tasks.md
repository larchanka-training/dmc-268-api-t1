# Tasks

- [x] 1.1 `call_with_user_token`: на `401` — `_refreshed(rejected=…)` под блокировкой (сменился — взять, жив refresh token — обменять) и один повтор; тесты
- [x] 1.2 `is_usable` с запасом 60 секунд; тест
- [x] 1.3 `_rate_limited`: «rate limit» в `message`; тест
- [x] 1.4 Порт `InstallationCatalog.user_repository` и `app_installed`; адаптер: `GET /repositories/{id}` токеном пользователя, `GET /repos/{owner}/{repo}/installation` с JWT; тесты на формах из документации
- [x] 1.5 `connect_repository`: два запроса, порядок «доступ → установка», один `404`; тесты: не виден, виден без приложения, приостановлено, read — `403`, admin/maintain — `201`, тела двух `404` совпадают, два запроса к GitHub
- [x] 1.6 Предупреждение о пропущенных установках; тест
- [x] 1.7 `logout` читает токен под блокировкой, при `ProviderTokenBusy` всё равно удаляет сессию; тесты
- [x] 1.8 Экспорт `docs/openapi/openapi.json`, README, `docs/BACKEND_ARCHITECTURE.md`; проверки с базой
