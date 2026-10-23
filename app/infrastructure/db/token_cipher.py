"""Шифрование токенов пользователя при хранении: AES-256-GCM.

Ключ — 32 байта из настройки `USER_TOKEN_ENCRYPTION_KEY` (base64,
`openssl rand -base64 32`). Формат шифротекста: байт версии, 12 байт
случайного nonce, затем шифротекст с тегом GCM. Associated data — id
сессии и имя поля: шифротекст, перенесённый в другую строку или в другое
поле, не расшифровывается (change `repositories-via-user-token`, design D2).

Неверный ключ, повреждённая или чужая запись — `ProviderTokenUnavailable`
без подробностей: ни ключа, ни открытого текста в сообщении нет.
"""

import os
from uuid import UUID

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from app.domain.provider_token import ProviderTokenUnavailable

KEY_BYTES = 32
_VERSION = b"\x01"
_NONCE_BYTES = 12
_AAD_PREFIX = b"dmc268/provider_tokens/v1/"


class TokenCipher:
    def __init__(self, key: bytes) -> None:
        if len(key) != KEY_BYTES:
            raise ValueError(f"ключ шифрования токенов — {KEY_BYTES} байта")
        self._aead = AESGCM(key)

    def __repr__(self) -> str:
        return "TokenCipher(<ключ скрыт>)"

    def encrypt(self, plaintext: str, session_id: UUID, field: str) -> bytes:
        nonce = os.urandom(_NONCE_BYTES)
        return _VERSION + nonce + self._aead.encrypt(nonce, plaintext.encode(), _aad(session_id, field))

    def decrypt(self, blob: bytes, session_id: UUID, field: str) -> str:
        if blob[:1] != _VERSION or len(blob) <= 1 + _NONCE_BYTES:
            raise ProviderTokenUnavailable("неизвестный формат шифротекста токена")
        nonce, ciphertext = blob[1 : 1 + _NONCE_BYTES], blob[1 + _NONCE_BYTES :]
        try:
            return self._aead.decrypt(nonce, ciphertext, _aad(session_id, field)).decode()
        except InvalidTag:
            raise ProviderTokenUnavailable(
                "токен не расшифровывается: другой ключ или чужая запись"
            ) from None


def _aad(session_id: UUID, field: str) -> bytes:
    return _AAD_PREFIX + session_id.bytes + b"/" + field.encode()
