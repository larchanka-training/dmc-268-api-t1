# Шаблон: адаптер и интеграционный тест

Адаптер в `app/infrastructure/db/` переводит строку в сущность и обратно. Ветвление допустимо
только как вызов функции домена — тогда это помечается комментарием.

```python
# app/infrastructure/db/repositories.py
class SqlAlchemy<Сущность>Repo:
    """<Порт, который реализует.>"""

    def __init__(self, session: Session) -> None:
        self._session = session

    def add(self, entity: <Сущность>) -> None:
        self._session.add(m.to_row(entity))

    def get(self, entity_id: UUID) -> <Сущность> | None:
        row = self._session.get(<Сущность>Row, entity_id)
        return m.to_entity(row) if row else None
```

Порт объявляется в `app/application/ports/repositories.py` как `Protocol`; связывание с
адаптером — только в `app/infrastructure/container.py`.

Тест с настоящей базой:

```python
# tests/db/test_<что>.py
import pytest

from ..conftest import requires_db

pytestmark = [pytest.mark.integration, requires_db]


def test_saved_entity_is_readable(uow) -> None:
    uow.<репозиторий>.add(entity)
    assert uow.<репозиторий>.get(entity.id) == entity
```

Результат читается тем же интерфейсом, а не сырым SQL. Проверка: `uv run pytest` пропускает без
`TEST_DATABASE_URL` и проходит с ним.
