"""Общее для тестов VCS-адаптера: тестовая пара ключей RSA.

Ключ генерируется один раз на сессию: 2048-битная генерация небыстрая, а
подписать тестам нужно всего несколько JWT.
"""

from dataclasses import dataclass

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa


@dataclass(frozen=True)
class RsaKeyPair:
    """PEM-строки: приватный ключ для подписи, публичный — для проверки."""

    private_pem: str
    public_pem: str


@pytest.fixture(scope="session")
def rsa_key_pair() -> RsaKeyPair:
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private_pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode()
    public_pem = (
        private_key.public_key()
        .public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        .decode()
    )
    return RsaKeyPair(private_pem=private_pem, public_pem=public_pem)
