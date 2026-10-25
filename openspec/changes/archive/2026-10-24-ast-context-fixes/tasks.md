## 1. Тесты-воспроизведения

- [x] 1.1 Красные тесты адаптера на настоящем tree-sitter: декораторы TS (несколько, многострочные аргументы, комментарий между ними, класс через `export`), generic-типы и функции Go с ограничениями, вложенная функция при слиянии объявлений TS (`tests/infrastructure/test_tree_sitter_extractor.py`)
- [x] 1.2 Красный инвариант `select_scopes` на литералах: вложенные функция, класс, интерфейс не тянут тип по имени (`tests/domain/test_code_context.py`)

## 2. Правка

- [x] 2.1 Диапазон от первого декоратора, сигнатура без декораторов, заголовок типа Go до узла типа (`app/infrastructure/parsing/tree_sitter_extractor.py`)
- [x] 2.2 Тип по имени родителя — только для метода (`app/domain/code_context.py`)

## 3. Проверки

- [x] 3.1 `uv run ruff check .`, `uv run ruff format --check .`, `uv run mypy .`, `uv run lint-imports`, `uv run pytest` без пропусков, `openspec validate --all --strict`
