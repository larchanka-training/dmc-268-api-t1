"""Проверка подписи `X-Hub-Signature-256`: формат GitHub и constant-time сравнение."""

import hashlib
import hmac

import pytest

from app.domain.hmac import verify_hmac

SECRET = b"github-webhook-secret"
BODY = b'{"action":"opened"}'


def _sign(body: bytes, secret: bytes = SECRET) -> str:
    """Подпись в формате заголовка GitHub: `sha256=<hex>`."""
    return "sha256=" + hmac.new(secret, body, hashlib.sha256).hexdigest()


def test_valid_signature_is_accepted() -> None:
    assert verify_hmac(BODY, _sign(BODY), SECRET) is True


def test_signature_of_another_body_is_rejected() -> None:
    """Тело подменено после подписания — подпись больше не сходится."""
    assert verify_hmac(b"tampered", _sign(BODY), SECRET) is False


def test_empty_signature_is_rejected() -> None:
    """Отсутствующий заголовок до функции доходит пустой строкой."""
    assert verify_hmac(BODY, "", SECRET) is False


def test_signature_made_with_wrong_secret_is_rejected() -> None:
    assert verify_hmac(BODY, _sign(BODY, secret=b"other-secret"), SECRET) is False


def test_signature_without_prefix_is_rejected() -> None:
    """Голый hex без префикса `sha256=` — не формат GitHub."""
    digest = hmac.new(SECRET, BODY, hashlib.sha256).hexdigest()
    assert verify_hmac(BODY, digest, SECRET) is False


@pytest.mark.parametrize(
    "signature",
    [
        pytest.param("sha256=", id="пустой hex"),
        pytest.param("sha256=" + "z" * 64, id="64 символа, но не hex"),
        pytest.param("sha256=" + "ab\xff", id="не-ASCII в hex"),
        pytest.param("sha1=" + "0" * 40, id="чужой алгоритм"),
    ],
)
def test_malformed_signature_is_rejected_without_raising(signature: str) -> None:
    """Битая подпись — ложь, а не исключение: эндпоинт ответит 401, не 500."""
    assert verify_hmac(BODY, signature, SECRET) is False


def test_comparison_goes_through_compare_digest(monkeypatch: pytest.MonkeyPatch) -> None:
    """Равенство проверяет `hmac.compare_digest`, а не `==`.

    Обычное сравнение строк реагирует на первый же различающийся байт и
    выдаёт подпись по времени ответа.

    Связка теста с реализацией сознательная: шпион стоит поверх
    `hmac.compare_digest`, потому что поведенчески (замером времени)
    constant-time в юнит-тесте доказать нельзя — проверяем механизм,
    который требуется по AC задачи.
    """
    compared: list[tuple[bytes, bytes]] = []
    real = hmac.compare_digest

    def spy(left: bytes, right: bytes) -> bool:
        compared.append((left, right))
        return real(left, right)

    monkeypatch.setattr(hmac, "compare_digest", spy)

    assert verify_hmac(BODY, _sign(BODY), SECRET) is True
    assert compared, "подпись сверена не через hmac.compare_digest"
