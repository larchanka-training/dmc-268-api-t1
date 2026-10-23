# Правила ревью acme/payments

Проектные ограничения, которых нет в стандартных критериях ревью: без этого
файла модели неоткуда о них узнать.

## Error Handling Standards

- pattern: `app/**/*.py`
- exclude: `**/tests/**`

1. Отказ в коде приложения возвращается значением Result, а не исключением
2. Пойманное исключение не глотается молча: оно обработано или переброшено с контекстом

## Clock Is Injected

- pattern: `app/domain/**/*.py`

1. Текущее время приходит аргументом: вызовы datetime.now(), datetime.utcnow() и time.time() в домене запрещены

## Money Is Decimal

- pattern: `app/**/*.py`
- exclude: `**/tests/**`

1. Денежные суммы представлены Decimal, а не float

## Logging

- pattern: `app/**/*.py`
- exclude: `**/tests/**`

1. Диагностика пишется через logging: print() в коде приложения запрещён
