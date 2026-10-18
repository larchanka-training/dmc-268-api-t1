"""Срок жизни сессии и хэш её cookie.

Значение cookie приходит аргументом: случайность — дело вызывающего кода.
В базу попадает только SHA-256 от значения, поэтому утечка таблицы не даёт
действующих cookie.
"""

import hashlib
from datetime import datetime, timedelta
from uuid import UUID

from app.domain.entities import Session

# 30 дней от входа, без продления.
SESSION_LIFETIME = timedelta(days=30)


def hash_token(token: str) -> bytes:
    """Хэш значения cookie, по которому сессию ищут в базе."""
    return hashlib.sha256(token.encode()).digest()


def open_session(session_id: UUID, user_id: UUID, token: str, now: datetime) -> Session:
    """Сессия, открытая входом в момент `now`."""
    return Session(
        id=session_id,
        token_hash=hash_token(token),
        user_id=user_id,
        created_at=now,
        expires_at=now + SESSION_LIFETIME,
    )


def is_expired(session: Session, now: datetime) -> bool:
    """Сессия недействительна начиная с `expires_at`."""
    return now >= session.expires_at
