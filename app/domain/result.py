"""A verdict a pure function can return without raising.

Rules live in functions that take data and return data, so a rejection is a
value the caller inspects rather than an exception that unwinds the stack.
"""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Result[T]:
    """Either a value or a reason it could not be produced."""

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
        """Return the value, or raise `ValueError` if this is a failure.

        Failure is decided by `error`, not by the value being None: a success
        may legitimately carry None. An `assert` would also disappear under
        `python -O`, taking the check with it.
        """
        if self.error is not None:
            raise ValueError(self.error)
        return self.value  # type: ignore[return-value]
