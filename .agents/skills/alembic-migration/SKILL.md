---
name: alembic-migration
description: Миграции Alembic в dmc-268. Используйте, когда меняете модели, добавляете таблицу, колонку или значение enum, чините откат или расхождение схемы с моделями.
---

# Миграция

Схему меняет только ревизия, прошедшая ревью. Приложение при старте ничего не создаёт.
Соглашения целиком — раздел «Миграции» в
[`docs/BACKEND_ARCHITECTURE.md`](../../../docs/BACKEND_ARCHITECTURE.md).

## Порядок

```bash
uv run alembic revision --autogenerate -m "<что меняется>"
# прочитать сгенерированное и починить
uv run alembic upgrade head
uv run alembic downgrade base
uv run alembic upgrade head
uv run alembic heads          # ровно одна строка
uv run pytest                 # с TEST_DATABASE_URL: тест сверяет модели со схемой
```

Имя файла — префикс с нулями: `0002_add_finding_category.py`.

## Что autogenerate делает не так

- Не удаляет нативные enum-типы в `downgrade`: таблицы уйдут, типы останутся, следующий
  `upgrade` упадёт на создании существующего типа. Удаление типов дописывается руками.
- Не видит того, чего нет в моделях: серверные значения по умолчанию, частичные индексы,
  `NULLS NOT DISTINCT` в уникальных ограничениях.
- Пропускает переименование: видит удаление и добавление колонки, то есть теряет данные.
  Переименование пишется руками через `op.alter_column(..., new_column_name=...)`.

## Значение в enum — отдельная миграция

`ALTER TYPE ... ADD VALUE` в старых версиях PostgreSQL не выполняется внутри транзакции:

```python
def upgrade() -> None:
    op.execute("COMMIT")
    op.execute("ALTER TYPE finding_category ADD VALUE 'maintainability'")
```

Удаление значения — пересоздание типа целиком, поэтому наборы держим небольшими.

## Две головы

Возникают, когда две ветки добавили ревизию от общего родителя. Чиним rebase'ом своей ветки на
`develop` и правкой `down_revision` в своей миграции, а не merge-ревизией.
