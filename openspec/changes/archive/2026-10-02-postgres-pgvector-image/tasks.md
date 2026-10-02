# Tasks

## 1. Стек

- [x] 1.1 Образ `pgvector/pgvector:pg18` в `infra/main.tf` и `docker-compose.yml` — строки дословно из #27
- [x] 1.2 Новое имя тома PostgreSQL в обоих описаниях

## 2. Сверка

- [x] 2.1 Тест: образ PostgreSQL в `docker-compose.yml` и `infra/main.tf` совпадает
- [x] 2.2 Локально: стек на новом образе поднимается с флагами стенда, миграции применяются, `amcheck` по индексам чистый

## 3. Проверка

- [x] 3.1 `pytest` против PostgreSQL, `tofu validate`, пробное слияние с #27
