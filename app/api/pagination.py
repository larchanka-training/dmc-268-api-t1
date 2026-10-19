"""Пагинация списков: `limit`/`offset`.

`limit` выше потолка не отклоняется, а приводится к `MAX_LIMIT` (design.md change'а
add-review-rest-api), поэтому верхней границы у него в схеме нет. У `offset` граница
есть: это максимум bigint — типа `OFFSET` в PostgreSQL. Больше база не примет и ответила
бы `500`; меньшая граница была бы выдуманной.
"""

from typing import Annotated

from fastapi import Query

DEFAULT_LIMIT = 50
MAX_LIMIT = 200
MAX_OFFSET = 2**63 - 1

Limit = Annotated[
    int, Query(ge=1, description=f"Размер страницы; по умолчанию {DEFAULT_LIMIT}, больше {MAX_LIMIT} приводится к {MAX_LIMIT}.")
]
Offset = Annotated[int, Query(ge=0, le=MAX_OFFSET, description="Сколько записей пропустить от начала списка.")]


def page_size(limit: int) -> int:
    """`limit` запроса, приведённый к потолку."""
    return min(limit, MAX_LIMIT)
