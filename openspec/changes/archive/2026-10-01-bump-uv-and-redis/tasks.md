# Tasks

## 1. Версии

- [x] 1.1 uv 0.12.21 в `ci.yml` (три джобы) и в `Dockerfile`; `git grep` не находит 0.12.12
- [x] 1.2 `redis:8-alpine` в `docker-compose.yml`

## 2. Приёмка

- [x] 2.1 Образ API собирается с uv 0.12.21, `uv sync --frozen` в нём проходит
- [x] 2.2 Redis 8 из compose поднимается и проходит healthcheck
- [x] 2.3 Зелёный CI

## Примечания к выполнению

2.1: образ собирается, в нём `uv 0.12.21`, импорты приложения проходят. 2.2: `redis:8-alpine` (8.10.2) из compose доходит до `healthy`.

2.3: [CI 36788711685](https://github.com/larchanka-training/dmc-268-api-t1/actions/runs/36788711685) зелёный, 127 тестов.
