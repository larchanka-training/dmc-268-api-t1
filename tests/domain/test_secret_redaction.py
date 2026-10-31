"""Секреты в сохраняемом контексте прогона заменяются меткой (#28)."""

import time
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
        "ASIAIOSFODNN7EXAMPLE",
        "xoxb-1234567890-abcdefghij",
        "xoxp-1234567890-abcdefghij",
        "sk-" + "proj_" + "x" * 30,
        "sk_live_" + "4eC39HqLyjWDarjtT1zdp7dc",
        "rk_test_" + "Z" * 24,
        "AIza" + "SyD-" + "a" * 31,
        "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.dozjgNryP4J3jVmNHl0w5N_XgL0n3I9PlFUP0THsR8U",
    ],
)
def test_a_known_secret_is_replaced(secret: str) -> None:
    assert redact_secrets(f'+API_KEY = "{secret}"  # prod') == f'+API_KEY = "{REDACTED}"  # prod'


@pytest.mark.parametrize(
    ("name", "secret"),
    [
        ("GITHUB_TOKEN_", "ghp_" + "a1B2" * 9),
        ("key_", "AKIAIOSFODNN7EXAMPLE"),
        ("api_", "sk-" + "x" * 30),
        ("slack_", "xoxb-1234567890-abcdefghij"),
        ("stripe_", "sk_live_" + "4eC39HqLyjWDarjtT1zdp7dc"),
        ("google_", "AIza" + "SyD-" + "a" * 31),
    ],
)
def test_a_secret_glued_to_an_underscore_is_replaced(name: str, secret: str) -> None:
    """Имя переменной перед токеном: `\\b` между `_` и буквой границы не видит."""
    assert redact_secrets(f"+{name}{secret} = 1") == f"+{name}{REDACTED} = 1"


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ('+url = "postgres://app:hunter2@db/x"', f'+url = "postgres://{REDACTED}@db/x"'),
        ("+redis://:hunter2@cache:6379", f"+redis://{REDACTED}@cache:6379"),
        ('+-H "Authorization: Basic dXNlcjpwYXNz"', f'+-H "{REDACTED}"'),
        ('+{"Authorization": "Basic dXNlcjpwYXNz"}', f'+{{"{REDACTED}"}}'),
    ],
)
def test_credentials_in_a_url_or_basic_auth_are_replaced(text: str, expected: str) -> None:
    assert redact_secrets(text) == expected


def test_a_private_key_spanning_diff_lines_is_replaced_whole() -> None:
    assert redact_secrets(f"+key = '''\n+{PEM}\n+'''\n+x = 1") == f"+key = '''\n+{REDACTED}\n+'''\n+x = 1"


@pytest.mark.parametrize(
    "secret",
    [
        "sk-proj-" + "A" * 40,
        "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.dozjgNryP4J3jVmNHl0w5N_XgL0n3I9PlFUP0THsR8U",
    ],
)
def test_a_bare_secret_on_a_removed_diff_line_is_replaced(secret: str) -> None:
    """PR, который удаляет закоммиченный ключ: строка диффа начинается с `-`."""
    assert redact_secrets(f"+x = 1\n-{secret}\n+y = 2") == f"+x = 1\n-{REDACTED}\n+y = 2"


def test_code_between_two_complete_keys_is_kept() -> None:
    assert redact_secrets(f"+{PEM}\n+x = 1\n+{PEM}") == f"+{REDACTED}\n+x = 1\n+{REDACTED}"


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
        "+if line.startswith('-----BEGIN PRIVATE KEY-----'):\n+    return parse(line)\n+x = 1",
        "+url = 'https://example.com/a:b@c'",
        "+Basic authentication is supported",
        "+password = os.environ['DB_PASSWORD']",
        "+ghp_short = 1",
        "+def ask(sk-learn): ...",
        "+header = 'eyJ'",
        "+task-sk-abcdefghijklmnopqrstuvw = 1",
        "+task-eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.dozjgNryP4J3jVmNHl0w5N_XgL0n3I9PlFUP0THsR8U",
    ],
)
def test_ordinary_code_is_left_as_is(text: str) -> None:
    assert redact_secrets(text) == text


def _seconds(text: str) -> float:
    """Лучшее из трёх: шум нагрузки CI только добавляет время."""
    best = float("inf")
    for _ in range(3):
        started = time.perf_counter()
        redact_secrets(text)
        best = min(best, time.perf_counter() - started)
    return best


@pytest.mark.parametrize("unit", ["--eyJ", "-eyJ-", "_eyJ", "_sk-", "://a:"])
def test_a_hostile_run_of_token_characters_is_scanned_in_linear_time(unit: str) -> None:
    """Вчетверо длиннее вход — линейный поиск вчетверо дольше, квадратичный в 16 раз.

    Сравнение двух размеров, а не порог в секундах: от скорости машины CI не зависит.
    """
    small = unit * (64_000 // len(unit))
    assert _seconds(small * 4) < 8 * _seconds(small)


def test_strings_are_redacted_at_any_depth_and_the_input_is_not_changed() -> None:
    body: dict[str, Any] = {
        "diff": [{"path": "a.py", "hunk": "+t = 'AKIAIOSFODNN7EXAMPLE'"}],
        "omitted_count": 2,
        "ast": None,
    }

    redacted = redact_secrets(body)

    assert redacted == {"diff": [{"path": "a.py", "hunk": f"+t = '{REDACTED}'"}], "omitted_count": 2, "ast": None}
    assert body["diff"][0]["hunk"] == "+t = 'AKIAIOSFODNN7EXAMPLE'"
