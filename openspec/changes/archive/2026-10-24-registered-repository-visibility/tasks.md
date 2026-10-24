## 1. Видимость зарегистрированных репозиториев

- [x] 1.1 `visible_provider_ids`, `is_visible`, `visible_repository` в `app/api/repositories.py`; `visible_run` — на том же правиле
- [x] 1.2 `GET /repositories` — только видимые, порядок и пагинация прежние; `GET /repositories/{id}` и `/pull-requests` — через `visible_repository`
- [x] 1.3 Тесты: список без невидимых и не-GitHub, пагинация видимых, невидимый — `404` как несуществующий, не-GitHub — `404`, сбой GitHub — `502`, отозванный токен — `401`, `422` без запроса к GitHub
- [x] 1.4 Экспорт OpenAPI
