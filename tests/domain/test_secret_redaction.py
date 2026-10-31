"""Секреты в сохраняемом контексте прогона заменяются меткой (#28)."""

from typing import Any

import pytest

from app.domain.secret_redaction import REDACTED, redact_secrets

PEM = "-----BEGIN RSA PRIVATE KEY-----\n+MIIEowIBAAKCAQEA0Z3VS5JJcds3xfn\n+-----END RSA PRIVATE KEY-----"


@pytest.mark.parametrize(
    "secret",
    [
        "ghp_" + "a1B2" * 9,
        "gho_" + "Z" * 36,
        "github_pat_" + "11ABCDEFG_" * 3,
        "AKIAIOSFODNN7EXAMPLE",
        "xoxb-1234567890-abcdefghij",
        "sk-" + "proj_" + "x" * 30,
        "sk_live_" + "4eC39HqLyjWDarjtT1zdp7dc",
        "rk_test_" + "Z" * 24,
        "AIza" + "SyD-" + "a" * 31,
        "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.dozjgNryP4J3jVmNHl0w5N_XgL0n3I9PlFUP0THsR8U",
    ],
)
def test_a_known_secret_is_replaced(secret: str) -> None:
    assert redact_secrets(f'+API_KEY = "{secret}"  # prod') == f'+API_KEY = "{REDACTED}"  # prod'


def test_a_private_key_spanning_diff_lines_is_replaced_whole() -> None:
    assert redact_secrets(f"+key = '''\n+{PEM}\n+'''\n+x = 1") == f"+key = '''\n+{REDACTED}\n+'''\n+x = 1"


def test_a_pgp_private_key_block_is_replaced_whole() -> None:
    pgp = "-----BEGIN PGP PRIVATE KEY BLOCK-----\n+lQOYBF0abc\n+-----END PGP PRIVATE KEY BLOCK-----"
    assert redact_secrets(f"+{pgp}\n+x = 1") == f"+{REDACTED}\n+x = 1"


def test_two_keys_with_one_end_are_replaced_together() -> None:
    """Перередакция — только в базе, а пропустить второй ключ нельзя."""
    text = f"+-----BEGIN PRIVATE KEY-----\n+AAA\n+y = 2\n+{PEM}\n+x = 1"
    assert redact_secrets(text) == f"+{REDACTED}\n+x = 1"


def test_a_private_key_cut_off_by_the_hunk_is_replaced_to_the_end() -> None:
    assert redact_secrets("+x = 1\n+-----BEGIN PRIVATE KEY-----\n+MIIEvQIBADANBg") == f"+x = 1\n+{REDACTED}"


@pytest.mark.parametrize(
    "text",
    [
        "+password = os.environ['DB_PASSWORD']",
        "+ghp_short = 1",
        "+def ask(sk-learn): ...",
        "+header = 'eyJ'",
        "+task-sk-abcdefghijklmnopqrstuvw = 1",
    ],
)
def test_ordinary_code_is_left_as_is(text: str) -> None:
    assert redact_secrets(text) == text


def test_strings_are_redacted_at_any_depth_and_the_input_is_not_changed() -> None:
    body: dict[str, Any] = {
        "diff": [{"path": "a.py", "hunk": "+t = 'AKIAIOSFODNN7EXAMPLE'"}],
        "omitted_count": 2,
        "ast": None,
    }

    redacted = redact_secrets(body)

    assert redacted == {"diff": [{"path": "a.py", "hunk": f"+t = '{REDACTED}'"}], "omitted_count": 2, "ast": None}
    assert body["diff"][0]["hunk"] == "+t = 'AKIAIOSFODNN7EXAMPLE'"
