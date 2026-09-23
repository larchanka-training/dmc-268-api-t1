"""Вердикт, который чистая функция может вернуть, не бросая исключение.

Правила живут в функциях, которые получают данные и возвращают данные, поэтому
отказ — это значение, которое разбирает вызывающий код, а не исключение,
разматывающее стек.
"""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Result[T]:
    """Либо значение, либо причина, по которой его не удалось получить."""

    value: T | None = None
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.error is None

    @classmethod
    def success(cls, value: T) -> Result[T]:
        return cls(value=value)

    @classmethod
    def failure(cls, error: str) -> Result[T]:
        return cls(error=error)

    def unwrap(self) -> T:
        """Вернуть значение или бросить `ValueError`, если это неудача.

        Неудачу определяет `error`, а не None в значении: успех вполне может
        нести None. К тому же `assert` исчез бы под `python -O` и унёс бы
        проверку с собой.
        """
        if self.error is not None:
            raise ValueError(self.error)
        return self.value  # type: ignore[return-value]
