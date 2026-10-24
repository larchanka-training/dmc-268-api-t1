## 1. Кэш видимых репозиториев

- [x] 1.1 `CachedInstallationCatalog`: `list_repositories` из `CacheStore` по sha256 токена, TTL из настроек; сбои GitHub не кэшируются, сбой хранилища — промах; `user_repository` и `app_installed` — мимо кэша
- [x] 1.2 `Container.visibility_catalog`, зависимость `get_visibility_catalog` у ручек видимости; страница подключения и подключение — без кэша
- [x] 1.3 Настройка `VISIBLE_REPOSITORIES_CACHE_TTL_SECONDS` (30 с), `.env.example`
- [x] 1.4 Тесты: повтор в пределах TTL без GitHub, после TTL — снова, другой токен — промах, токена в кэше нет, сбой GitHub не кэшируется, сломанный кэш — промах; API — повтор списка репозиториев и прогонов без GitHub, подключённый виден сразу, страница подключения всегда к GitHub
- [x] 1.5 `docs/BACKEND_ARCHITECTURE.md`
- [x] 1.6 По ревью PR: страница подключения пишет свежий ответ в кэш (`read=False`, `get_refreshing_catalog`), тест «приложение поставлено в пределах TTL → подключён → в списке»; `InMemoryCacheStore` под замком; комментарии порта и адаптера
