"""Проверка подписи вебхука — без HTTP, базы и брокера."""

import hashlib
import hmac

from app.domain.webhook_signature import verify_webhook_signature

SECRET = "s3cret"
BODY = b'{"action": "opened"}'


def _sign(body: bytes = BODY, secret: str = SECRET) -> str:
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def test_correct_signature_is_valid() -> None:
    assert verify_webhook_signature(SECRET, BODY, _sign()) is True


def test_missing_header_is_invalid() -> None:
    assert verify_webhook_signature(SECRET, BODY, None) is False
    assert verify_webhook_signature(SECRET, BODY, "") is False


def test_header_without_the_prefix_is_invalid() -> None:
    assert verify_webhook_signature(SECRET, BODY, _sign().removeprefix("sha256=")) is False


def test_signature_of_another_body_or_secret_is_invalid() -> None:
    assert verify_webhook_signature(SECRET, BODY, _sign(b"other")) is False
    assert verify_webhook_signature(SECRET, BODY, _sign(secret="other")) is False


def test_non_ascii_header_is_invalid_not_an_error() -> None:
    assert verify_webhook_signature(SECRET, BODY, "sha256=\xff") is False
