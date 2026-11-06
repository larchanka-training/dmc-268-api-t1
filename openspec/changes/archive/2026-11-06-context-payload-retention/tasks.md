## 1. Замер

- [x] 1.1 База с 51 000 прогонов: время и число SQL у запросов кабинета, план запроса стирания

## 2. Хранение

- [x] 2.1 Миграция `0019`: `body` nullable, `body_expired_at`, check, частичный индекс; откат
- [x] 2.2 Модель, сущность, маппер; `expire_bodies` в порте, адаптере и фейке
- [x] 2.3 Тесты адаптера на базе: стирание с метаданными, порядок и лимит, повтор не трогает стёртое, check

## 3. Проход воркера

- [x] 3.1 `expire_context_payloads` и тесты на фейке: граница срока, пачка, уже стёртое
- [x] 3.2 Шаг в `build_maintenance`, настройки `CONTEXT_PAYLOAD_RETENTION_DAYS` и `CONTEXT_PAYLOAD_EXPIRY_BATCH`, тест прохода

## 4. Завершение

- [x] 4.1 `docs/erd.md`, `docs/BACKEND_ARCHITECTURE.md`, `.env.example`
- [x] 4.2 `make check`, `pytest` с базой, синхронизация спек и архив change
