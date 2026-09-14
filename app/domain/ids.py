"""Identifier generation, kept in one place.

UUIDv7 rather than uuid4: the leading 48 bits are a millisecond timestamp, so
keys sort in creation order and inserts land at the right edge of the index
instead of scattering across it. Available in the standard library from 3.14.

This is the only function in the domain that is not pure. Entities take their
id as an argument so they stay constructible from literals in a test; callers
that need a fresh one ask here.
"""

import uuid


def new_id() -> uuid.UUID:
    """Return a fresh time-ordered identifier."""
    return uuid.uuid7()
