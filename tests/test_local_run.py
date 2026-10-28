"""Инструменты локального запуска против настоящего GitHub: make local-doctor и local-env.

Главное, что держат тесты: секреты не попадают в вывод, заполненный `.env` не перетирается.
"""

import base64
import shutil
import subprocess
from pathlib import Path

import pytest

from app.config import Settings
from scripts import check_local_env
from scripts.check_local_env import Level

ROOT = Path(__file__).resolve().parent.parent
INIT_SCRIPT = ROOT / "scripts" / "init_local_env.sh"

PEM = "-----BEGIN RSA PRIVATE KEY-----\nMIIEsecretbody\n-----END RSA PRIVATE KEY-----"
SECRETS = {
    "github_app_private_key": PEM,
    "github_app_client_secret": "client-secret-value",
    "github_webhook_secret": "webhook-secret-value",
    "user_token_encryption_key": base64.b64encode(b"k" * 32).decode(),
}


def settings(**overrides: object) -> Settings:
    values: dict[str, object] = {
        "database_url": "postgresql+psycopg://u:p@localhost/db",
        "rabbitmq_url": "amqp://guest:guest@localhost//",
        "github_app_id": "12345",
        "github_app_client_id": "Iv1.client",
        "public_origin": "http://localhost:8000",
        **SECRETS,
        **overrides,
    }
    return Settings(_env_file=None, **values)  # type: ignore[arg-type]


def run_checks(env_file: Path, **overrides: object) -> list[check_local_env.Check]:
    return check_local_env.check_settings(lambda: settings(**overrides), env_file)


def failures(checks: list[check_local_env.Check]) -> list[str]:
    return [check.text for check in checks if check.level is Level.FAIL]


@pytest.fixture
def env_file(tmp_path: Path) -> Path:
    path = tmp_path / ".env"
    path.write_text("")
    return path


def test_complete_settings_pass_and_print_no_secret(env_file: Path) -> None:
    checks = run_checks(env_file)

    assert failures(checks) == []
    output = check_local_env.render([("Настройки", checks)])
    for secret in SECRETS.values():
        assert secret not in output
    assert "MIIEsecretbody" not in output
    assert "http://localhost:8000/api/v1/auth/github/callback" in output


@pytest.mark.parametrize(
    ("override", "expected"),
    [
        ({"github_app_id": ""}, "GITHUB_APP_ID не задан"),
        ({"github_app_private_key": ""}, "GITHUB_APP_PRIVATE_KEY не задан"),
        ({"github_app_client_id": ""}, "GITHUB_APP_CLIENT_ID не задан"),
        ({"github_app_client_secret": ""}, "GITHUB_APP_CLIENT_SECRET не задан"),
        ({"user_token_encryption_key": ""}, "USER_TOKEN_ENCRYPTION_KEY не задан"),
        ({"public_origin": ""}, "PUBLIC_ORIGIN не задан"),
    ],
)
def test_missing_setting_fails(
    env_file: Path, override: dict[str, object], expected: str
) -> None:
    assert failures(run_checks(env_file, **override)) == [expected]


def test_pem_without_line_breaks_fails(env_file: Path) -> None:
    flat = PEM.replace("\n", " ")

    checks = run_checks(env_file, github_app_private_key=flat)

    assert failures(checks) == [
        "GITHUB_APP_PRIVATE_KEY не похож на PEM с переносами строк"
    ]
    assert flat not in check_local_env.render([("", checks)])


def test_placeholder_webhook_secret_fails(env_file: Path) -> None:
    checks = run_checks(env_file, github_webhook_secret="local-dev-secret")

    assert failures(checks) == ["GITHUB_WEBHOOK_SECRET — заглушка local-dev-secret"]


def test_missing_env_file_fails(tmp_path: Path) -> None:
    assert failures(run_checks(tmp_path / ".env")) == [f"{tmp_path / '.env'} нет"]


def test_invalid_setting_reports_field_without_value(env_file: Path) -> None:
    bad_key = "not-base64-but-secret!"

    checks = check_local_env.check_settings(
        lambda: settings(user_token_encryption_key=bad_key), env_file
    )

    output = check_local_env.render([("", checks)])
    assert "USER_TOKEN_ENCRYPTION_KEY" in output
    assert bad_key not in output
    assert failures(checks)


def test_missing_tools_fail_but_cloudflared_only_warns() -> None:
    checks = check_local_env.check_tools(which=lambda tool: None)

    levels = {check.text: check.level for check in checks}
    assert levels["docker не установлен"] is Level.FAIL
    assert levels["ollama не установлен"] is Level.FAIL
    cloudflared = [check for check in checks if check.text.startswith("cloudflared")]
    assert [check.level for check in cloudflared] == [Level.WARN]


def test_stopped_docker_daemon_fails() -> None:
    checks = check_local_env.check_tools(
        which=lambda tool: f"/bin/{tool}", docker_up=lambda: False
    )

    assert failures(checks) == ["Docker не отвечает"]


def run_init(cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(INIT_SCRIPT)], cwd=cwd, capture_output=True, text=True, check=True
    )


@pytest.fixture
def repo_copy(tmp_path: Path) -> Path:
    shutil.copy(ROOT / ".env.example", tmp_path / ".env.example")
    return tmp_path


def env_value(path: Path, name: str) -> str:
    lines = [
        line for line in path.read_text().splitlines() if line.startswith(f"{name}=")
    ]
    assert len(lines) == 1
    return lines[0].removeprefix(f"{name}=")


def test_init_creates_env_with_key_and_does_not_print_it(repo_copy: Path) -> None:
    result = run_init(repo_copy)

    env = repo_copy / ".env"
    key = env_value(env, "USER_TOKEN_ENCRYPTION_KEY")
    assert len(base64.b64decode(key, validate=True)) == 32
    assert key not in result.stdout + result.stderr
    assert env.stat().st_mode & 0o077 == 0


def test_init_keeps_filled_values(repo_copy: Path) -> None:
    env = repo_copy / ".env"
    env.write_text("GITHUB_APP_ID=777\nUSER_TOKEN_ENCRYPTION_KEY=existing\n")

    run_init(repo_copy)

    assert env.read_text() == "GITHUB_APP_ID=777\nUSER_TOKEN_ENCRYPTION_KEY=existing\n"


def test_init_fills_empty_key_in_existing_env(repo_copy: Path) -> None:
    env = repo_copy / ".env"
    env.write_text("GITHUB_APP_ID=777\nUSER_TOKEN_ENCRYPTION_KEY=\n")

    run_init(repo_copy)

    assert env_value(env, "GITHUB_APP_ID") == "777"
    assert (
        len(
            base64.b64decode(env_value(env, "USER_TOKEN_ENCRYPTION_KEY"), validate=True)
        )
        == 32
    )
