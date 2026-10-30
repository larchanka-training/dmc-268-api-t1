"""Проверка окружения локального запуска против настоящего GitHub (шаг make run-local).

Ничего не меняет: смотрит инструменты и настройки из окружения и `.env`, печатает ✓/!/✗ и
подсказку. Значения секретов не печатает никогда — только «задан / не задан»; ошибка
разбора настроек выводится именем поля и сообщением валидатора, без значения: `Settings`
скрывает ввод в ошибках (`hide_input_in_errors`). Проверяет всё до конца, чтобы показать все
проблемы разом; код 1 — есть ✗.

    uv run python -m scripts.check_local_env
"""

import shutil
import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from pydantic import ValidationError

from app.config import Settings

ENV_FILE = Path(".env")
# Заглушка из .env.example и docker-compose.yml: настоящая App подписывает вебхуки своим секретом.
PLACEHOLDER_WEBHOOK_SECRET = "local-dev-secret"
CALLBACK_PATH = "/api/v1/auth/github/callback"


class Level(StrEnum):
    OK = "✓"
    WARN = "!"
    FAIL = "✗"


@dataclass(frozen=True)
class Check:
    level: Level
    text: str
    hint: str = ""


def check_tools(
    which: Callable[[str], str | None] = shutil.which,
    docker_up: Callable[[], bool] | None = None,
) -> list[Check]:
    """Инструменты на машине: uv, docker с запущенным демоном, ollama, cloudflared."""
    checks: list[Check] = []
    required = {
        "uv": "curl -LsSf https://astral.sh/uv/install.sh | sh  (или brew install uv)",
        "curl": "поставьте curl пакетным менеджером системы",
        "openssl": "поставьте openssl пакетным менеджером системы",
        "ollama": "brew install ollama  (или https://ollama.com/download)",
        "docker": "Docker Desktop, Rancher Desktop или OrbStack",
    }
    for tool, hint in required.items():
        if which(tool):
            checks.append(Check(Level.OK, tool))
        else:
            checks.append(Check(Level.FAIL, f"{tool} не установлен", hint))
    if which("docker"):
        is_up = docker_up if docker_up is not None else _docker_daemon_up
        if is_up():
            checks.append(Check(Level.OK, "Docker запущен"))
        else:
            checks.append(
                Check(
                    Level.FAIL,
                    "Docker не отвечает",
                    "запустите Docker Desktop / Rancher Desktop",
                )
            )
    if which("cloudflared"):
        checks.append(Check(Level.OK, "cloudflared"))
    else:
        checks.append(
            Check(
                Level.WARN,
                "cloudflared не установлен — без туннеля GitHub не достучится до вебхука",
                "brew install cloudflared  (или запуск с TUNNEL=0 и свой туннель)",
            )
        )
    return checks


def _docker_daemon_up() -> bool:
    try:
        result = subprocess.run(
            ["docker", "info"], capture_output=True, timeout=15, check=False
        )
    except subprocess.TimeoutExpired, OSError:
        return False
    return result.returncode == 0


def check_settings(
    load: Callable[[], Settings] = Settings, env_file: Path = ENV_FILE
) -> list[Check]:
    """Настройки GitHub App и входа: заданы ли, без значений."""
    checks: list[Check] = []
    if env_file.is_file():
        checks.append(Check(Level.OK, f"{env_file} есть"))
    else:
        checks.append(
            Check(
                Level.FAIL,
                f"{env_file} нет",
                "make run-local создаст его из .env.example",
            )
        )
    try:
        settings = load()
    except ValidationError as exc:
        for error in exc.errors():
            field = ".".join(str(part) for part in error["loc"]) or "настройки"
            checks.append(
                Check(
                    Level.FAIL,
                    f"настройка {field.upper()}: {error['msg']}",
                    "поправьте в .env",
                )
            )
        return checks

    def required(ok: bool, name: str, hint: str) -> None:
        checks.append(
            Check(Level.OK, name) if ok else Check(Level.FAIL, f"{name} не задан", hint)
        )

    from_app = "из настроек GitHub App, в .env"
    required(
        bool(settings.github_app_id.strip()), "GITHUB_APP_ID", f"App ID {from_app}"
    )
    key = settings.github_app_private_key.get_secret_value().strip()
    if not key:
        checks.append(
            Check(
                Level.FAIL,
                "GITHUB_APP_PRIVATE_KEY не задан",
                "PEM целиком, в двойных кавычках",
            )
        )
    elif not key.startswith("-----BEGIN") or "\n" not in key:
        checks.append(
            Check(
                Level.FAIL,
                "GITHUB_APP_PRIVATE_KEY не похож на PEM с переносами строк",
                'значение в двойных кавычках, на нескольких строках: KEY="-----BEGIN …\\n…"',
            )
        )
    else:
        checks.append(Check(Level.OK, "GITHUB_APP_PRIVATE_KEY"))
    required(
        bool(settings.github_app_client_id.strip()),
        "GITHUB_APP_CLIENT_ID",
        f"Client ID {from_app}",
    )
    required(
        bool(settings.github_app_client_secret.get_secret_value().strip()),
        "GITHUB_APP_CLIENT_SECRET",
        f"сгенерируйте client secret {from_app}",
    )
    if settings.github_webhook_secret.get_secret_value() == PLACEHOLDER_WEBHOOK_SECRET:
        checks.append(
            Check(
                Level.FAIL,
                f"GITHUB_WEBHOOK_SECRET — заглушка {PLACEHOLDER_WEBHOOK_SECRET}",
                "поставьте webhook secret из настроек App, иначе вебхуки получат 401",
            )
        )
    else:
        checks.append(Check(Level.OK, "GITHUB_WEBHOOK_SECRET"))
    required(
        bool(settings.user_token_encryption_key.get_secret_value().strip()),
        "USER_TOKEN_ENCRYPTION_KEY",
        "make run-local сгенерирует его",
    )
    if settings.public_origin:
        # Origin не секрет: печатаем callback, его же регистрируют в App.
        checks.append(
            Check(
                Level.OK,
                f"PUBLIC_ORIGIN, callback в App: {settings.public_origin}{CALLBACK_PATH}",
            )
        )
    else:
        checks.append(
            Check(
                Level.FAIL,
                "PUBLIC_ORIGIN не задан",
                "PUBLIC_ORIGIN=http://localhost:8000",
            )
        )
    return checks


def render(sections: list[tuple[str, list[Check]]]) -> str:
    lines: list[str] = []
    for title, checks in sections:
        lines.append(title)
        for check in checks:
            lines.append(f"  {check.level} {check.text}")
            if check.hint and check.level is not Level.OK:
                lines.append(f"    {check.hint}")
    return "\n".join(lines)


def main() -> int:
    sections = [
        ("Инструменты", check_tools()),
        ("Настройки (.env и окружение)", check_settings()),
    ]
    print(render(sections))
    errors = sum(
        check.level is Level.FAIL for _, checks in sections for check in checks
    )
    if errors:
        print(f"\nОкружение не готово: ошибок — {errors}.")
        return 1
    print("\nОкружение готово.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
