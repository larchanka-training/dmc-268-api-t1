"""Declarative base and the two columns every table carries.

`created_at` and `updated_at` are declared with `DateTime(timezone=True)`
explicitly. A bare `Mapped[datetime]` compiles to TIMESTAMP WITHOUT TIME ZONE,
which looks correct in review and is wrong in production: a run that started
before a daylight-saving shift would appear to finish before it began.

Identifiers are UUIDv7 supplied by the domain, never a database default, so an
entity is complete in memory before anything touches Postgres.
"""

import datetime as dt
import uuid

from sqlalchemy import DateTime, Uuid, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    """Shared metadata for every mapped table."""


class TimestampMixin:
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


class UuidPrimaryKeyMixin:
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)
