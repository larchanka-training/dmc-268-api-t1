## Why

Вкладка «Ревью» в кабинете (larchanka-training/dmc-268-ui-t1#44) показывает прогоны всех репозиториев, и фильтр по репозиторию там нужен с выбором нескольких сразу. В заглушке `GET /api/v1/reviews` из #105 `repository_id` принимает одно значение, а `status` — несколько. Ручка ещё не реализована (#103), поэтому параметр дешевле поменять сейчас, до того как фронтенд синхронизирует типы.

## What Changes

- `repository_id` в `GET /api/v1/reviews` повторяется, как `status`: `?repository_id=…&repository_id=…`, прогоны любого из перечисленных репозиториев.
- Невалидный UUID среди значений — `422` с ошибкой в `query.repository_id.<индекс>`.

## Impact

- Код: `ReviewRunListQuery` в `app/api/reviews.py`, тесты в `tests/test_problem_details.py`.
- Контракт: `repository_id` становится массивом UUID. Ручка — заглушка `501`, клиентов у неё нет; фронтенд подхватит тип через `pnpm api:sync`.
