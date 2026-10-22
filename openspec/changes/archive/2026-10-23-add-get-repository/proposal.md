## Why

Экран настроек репозитория (larchanka-training/dmc-268-ui-t1#29) узнаёт свой репозиторий, загружая `GET /repositories?limit=200` и ища id в нём. У 201-го подключённого репозитория экран показывает «Репозиторий не найден», хотя он есть. Ручки для одного репозитория в API нет.

## What Changes

- `GET /api/v1/repositories/{repository_id}` — один репозиторий в форме строки списка; `404 REPOSITORY_NOT_FOUND`, если его нет. Доступ — как у списка: любой с сессией.
- Описание `min_severity` в настройках приведено к решению из larchanka-training/dmc-268-ui-t1#28: порог для merge gate и вердикта «запрос изменений», а не фильтр публикации.

## Impact

- Код: `app/api/repositories.py`, тесты в `tests/test_repositories_api.py`.
- Контракт: новая операция, существующие не меняются.
