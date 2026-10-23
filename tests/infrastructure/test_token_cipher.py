"""Шифрование токенов пользователя: туда-обратно, чужой ключ, чужая строка."""

import os
from uuid import UUID

import pytest

from app.domain.provider_token import ProviderTokenUnavailable
from app.infrastructure.db.token_cipher import TokenCipher

KEY = bytes(range(32))
SESSION = UUID(int=1)
TOKEN = "ghu_16C7e42F292c6912E7710c838347Ae178B4a"


def test_round_trip() -> None:
    cipher = TokenCipher(KEY)
    blob = cipher.encrypt(TOKEN, SESSION, "access_token")

    assert TOKEN.encode() not in blob
    assert cipher.decrypt(blob, SESSION, "access_token") == TOKEN


def test_each_encryption_uses_a_fresh_nonce() -> None:
    cipher = TokenCipher(KEY)
    assert cipher.encrypt(TOKEN, SESSION, "access_token") != cipher.encrypt(TOKEN, SESSION, "access_token")


def test_another_key_cannot_decrypt() -> None:
    blob = TokenCipher(KEY).encrypt(TOKEN, SESSION, "access_token")

    with pytest.raises(ProviderTokenUnavailable) as raised:
        TokenCipher(os.urandom(32)).decrypt(blob, SESSION, "access_token")
    assert TOKEN not in str(raised.value)
    assert raised.value.__cause__ is None


@pytest.mark.parametrize(("session", "field"), [(UUID(int=2), "access_token"), (SESSION, "refresh_token")])
def test_ciphertext_moved_to_another_row_or_field_does_not_decrypt(session: UUID, field: str) -> None:
    cipher = TokenCipher(KEY)
    blob = cipher.encrypt(TOKEN, SESSION, "access_token")

    with pytest.raises(ProviderTokenUnavailable):
        cipher.decrypt(blob, session, field)


@pytest.mark.parametrize("blob", [b"", b"\x02" + bytes(40), b"\x01" + bytes(5)])
def test_garbage_is_unavailable_not_a_crash(blob: bytes) -> None:
    with pytest.raises(ProviderTokenUnavailable):
        TokenCipher(KEY).decrypt(blob, SESSION, "access_token")


def test_a_short_key_is_refused() -> None:
    with pytest.raises(ValueError, match="32"):
        TokenCipher(bytes(16))


def test_repr_hides_the_key() -> None:
    assert KEY.hex() not in repr(TokenCipher(KEY))
    assert "скрыт" in repr(TokenCipher(KEY))
