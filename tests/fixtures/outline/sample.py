import os
from collections.abc import Iterable


@dataclass(frozen=True)
class Repo(Base, Mixin):
    """Хранилище."""

    def save(self, item: Item) -> None:
        self._items.append(item)

    async def load(
        self,
        item_id: int,
    ) -> Item:
        def inner() -> int:
            return 1

        return self._items[item_id]


def helper(values: Iterable[int]) -> int:
    import json

    return sum(values)
