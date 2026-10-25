# Tasks

## 1. Сторож по имени тома

- [x] 1.1 Красные тесты: `delete` и замена `docker_volume` по чужому адресу с `before.name` защищённого тома сторож пропускает; удаление незащищённого тома по имени и имя защищённого тома у ресурса другого типа — разрешены
- [x] 1.2 `PROTECTED` — адрес → имя тома; `protected_deletes` ловит `delete` у `docker_volume` по `change.before.name`, проверка по адресу остаётся
- [x] 1.3 Тест сверяет имена в `PROTECTED` с `name` томов в `infra/*.tf`
- [x] 1.4 `infra/README.md`: сторож узнаёт том и по имени

## 2. Приёмка

- [x] 2.1 ruff, lint-imports, mypy, pytest с `PYTEST_FAIL_ON_SKIP=1`, openspec validate
