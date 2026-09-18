# Шаблон: решение домена

Функция над данными в `app/domain/`. Ни I/O, ни часов, ни случайности: время и идентификаторы
приходят аргументами. Отказ — значение `Result`, а не исключение.

```python
# app/domain/<область>.py
"""<Одна строка: какое решение принимает модуль.>"""

import datetime as dt

from app.domain.entities import ReviewRun
from app.domain.result import Result


def <решение>(run: ReviewRun, now: dt.datetime) -> Result[ReviewRun]:
    """<Что решает и когда отказывает.>"""
    if <условие отказа>:
        return Result.failure(f"<почему, с данными из аргументов>")
    return Result.success(<новое значение>)
```

Тест рядом, без базы:

```python
# tests/domain/test_<область>.py
def test_<что_проверяем>() -> None:
    verdict = <решение>(run, NOW)
    assert not verdict.ok
    assert "<фрагмент причины>" in (verdict.error or "")
```

Правило перебирается параметризацией, если оно про множество значений:

```python
@pytest.mark.parametrize("status", sorted(TERMINAL_STATUSES))
def test_terminal_run_is_refused(status: ReviewRunStatus) -> None:
    assert not <решение>(replace(run, status=status), NOW).ok
```

Проверка: `uv run pytest`, `uv run ruff check .`.
