# Tasks

## 1. Сторож по имени тома

- [x] 1.1 Красные тесты: `delete` и замена `docker_volume` по чужому адресу и с новым именем тома сторож пропускает; `forget` и удаление контейнера — разрешены
- [x] 1.2 `protected_deletes` ловит `delete` у любого `docker_volume`; список `PROTECTED` удалён
- [x] 1.3 Тест `prevent_destroy` — у каждого тома из `infra/*.tf`
- [x] 1.4 `infra/README.md`: сторож отклоняет удаление любого тома

## 2. Приёмка

- [x] 2.1 ruff, lint-imports, mypy, pytest с `PYTEST_FAIL_ON_SKIP=1`, openspec validate
