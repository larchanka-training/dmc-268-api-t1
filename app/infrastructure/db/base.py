"""Declarative base и две колонки, которые есть у каждой таблицы.

`created_at` и `updated_at` объявлены явно через `DateTime(timezone=True)`.
Голый `Mapped[datetime]` компилируется в TIMESTAMP WITHOUT TIME ZONE: на ревью
выглядит правильно, а в проде неверно — прогон, начавшийся до перевода часов,
окажется завершённым раньше, чем начался.

Идентификаторы — UUIDv7, которые выдаёт домен, а не значение по умолчанию в
базе, поэтому сущность целиком собрана в памяти до того, как её увидит
Postgres.
"""

import datetime as dt
import uuid

from sqlalchemy import DateTime, Uuid, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    """Общие метаданные для всех отображённых таблиц."""


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
