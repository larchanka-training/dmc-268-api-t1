# Tasks

## 1. Домен

- [x] 1.1 `select_scopes` (`app/domain/code_context.py`): тип по имени родителя не ищется, если метод лежит внутри выбранного типа с этим именем; тесты: метод вложенного класса не тянет модульный, метод TS `class Money` не тянет `interface Money`, метод Go получает тип ресивера и при затронутом локальном одноимённом типе

## 2. Адаптер tree-sitter

- [x] 2.1 `TreeSitterDefinitionExtractor`: `pair` в держателях функции с именем из `key`, `function_signature` в функциях, `_signature` срезает хвостовой `;`; тесты на TS и JS: метод объектного литерала, перегрузки и `declare function`

## 3. Проверка

- [x] 3.1 `ruff`, `mypy`, `lint-imports`, `pytest`, `openspec validate --all --strict`
