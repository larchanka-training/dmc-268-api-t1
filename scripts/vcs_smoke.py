"""Ручная smoke-проверка связности VCS-шлюза с реальным GitHub.

Диагностика, а не тест: гоняет продовый код `GitHubAppAuth` +
`GitHubVcsGateway` против `api.github.com` и печатает результат каждой
ступени. В pytest его сознательно не кладём — CI считает пропущенный тест
провалом (`.github/workflows/ci.yml`), а живой GitHub в CI недоступен.

Окружение читает напрямую, минуя `app.config.Settings`: Settings требует
`database_url` и `rabbitmq_url`, которые для проверки GitHub не нужны и
ввели бы в заблуждение. Скрипт — не процесс сервиса, поэтому отступление от
правила «окружение читает только `config.py`» здесь осознанное.

Запуск:

    GITHUB_APP_ID=... uv run python scripts/vcs_smoke.py \
        --repo <owner>/<name> --pr <номер> \
        --installation-id <id> \
        --key-file ~/.config/botyanya.private-key.pem

`installation_id` — из URL страницы установки GitHub App
(`github.com/settings/installations/<id>`), App ID и PEM — там же, в
настройках приложения. App нужны права Pull requests: Read-only и
Contents: Read-only.
"""

import argparse
import os
import sys
from pathlib import Path

import httpx

# Скрипт живёт вне пакета и запускается без установки: корень репозитория
# добавляется в sys.path, чтобы импорт app.* работал как в тестах (там его
# обеспечивает pythonpath в pyproject.toml).
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.infrastructure.vcs.errors import (
    VcsAuthError,
    VcsError,
    VcsUnavailableError,
)
from app.infrastructure.vcs.github import GitHubVcsGateway
from app.infrastructure.vcs.github_auth import GitHubAppAuth

_GITHUB_API = "https://api.github.com"
_TIMEOUT_SECONDS = 10.0


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Проверка связности VCS-шлюза с реальным GitHub",
    )
    parser.add_argument("--repo", required=True, help="полное имя репозитория, owner/name")
    parser.add_argument("--pr", required=True, type=int, help="номер пул-реквеста")
    parser.add_argument(
        "--installation-id",
        required=True,
        type=int,
        help="installation_id установки GitHub App на репозиторий",
    )
    parser.add_argument(
        "--key-file",
        type=Path,
        default=None,
        help="PEM-приватный ключ GitHub App; без него читается GITHUB_APP_PRIVATE_KEY",
    )
    return parser.parse_args()


def _private_key(args: argparse.Namespace) -> str:
    """PEM-ключ из файла или окружения; в окружении переносы строк эскейплены."""
    key_file: Path | None = args.key_file
    if key_file is not None:
        return key_file.read_text(encoding="utf-8")
    from_env = os.environ.get("GITHUB_APP_PRIVATE_KEY", "")
    if from_env:
        return from_env.replace("\\n", "\n")
    raise ValueError("нет приватного ключа: передайте --key-file или GITHUB_APP_PRIVATE_KEY")


def _hint(error: Exception) -> str:
    text = str(error)
    if isinstance(error, VcsAuthError):
        return (
            "GitHub отклонил авторизацию: проверьте App ID, приватный ключ "
            "(совпадает ли с текущим в настройках App) и installation_id"
        )
    if isinstance(error, VcsUnavailableError):
        return "GitHub недоступен по сети или отвечал 429/5xx трижды подряд"
    if "HTTP 403" in text:
        return "нет права на эндпоинт: у App должны быть Pull requests: Read-only и Contents: Read-only"
    if "HTTP 404" in text:
        return (
            "репозиторий, номер PR или installation_id не найдены: App "
            "установлена не на тот репозиторий?"
        )
    return "смотрите текст ошибки выше; детали запросов — в настройках App на GitHub"


def main() -> int:
    args = _parse_args()
    app_id = os.environ.get("GITHUB_APP_ID", "")
    if not app_id:
        print("ошибка: не задан GITHUB_APP_ID", file=sys.stderr)
        return 1
    try:
        private_key = _private_key(args)
    except ValueError as error:
        print(f"ошибка: {error}", file=sys.stderr)
        return 1
    except OSError as error:
        print(f"ошибка: ключ не прочитан: {error}", file=sys.stderr)
        return 1

    # Тот же состав, что у Container.vcs_gateway(), но без контейнера:
    # база и брокер для проверки GitHub не нужны.
    with httpx.Client(base_url=_GITHUB_API, timeout=_TIMEOUT_SECONDS) as client:
        auth = GitHubAppAuth(app_id, private_key, client)
        gateway = GitHubVcsGateway(auth, client)
        try:
            print(f"[1/3] installation token для {args.installation_id}...")
            auth.installation_token(args.installation_id)
            print("      получен")

            print(f"[2/3] метаданные {args.repo}#{args.pr}...")
            metadata = gateway.fetch_pr_metadata(args.repo, args.pr, args.installation_id)
            print(f"      #{metadata.number} «{metadata.title}»")
            print(f"      автор {metadata.author}, состояние {metadata.state.value}")
            print(f"      {metadata.source_branch} -> {metadata.target_branch}")
            print(f"      base {metadata.base_sha[:12]}... head {metadata.head_sha[:12]}")

            print("[3/3] дифф compare base...head...")
            diff = gateway.fetch_diff(
                args.repo, metadata.base_sha, metadata.head_sha, args.installation_id
            )
        except (VcsAuthError, VcsUnavailableError, VcsError) as error:
            print(f"провал: {error}", file=sys.stderr)
            print(f"подсказка: {_hint(error)}", file=sys.stderr)
            return 1

    files = sum(1 for line in diff.splitlines() if line.startswith("diff --git "))
    if not diff.strip():
        print(
            "провал: дифф пустой — у PR нет изменений или SHA совпадают",
            file=sys.stderr,
        )
        return 1
    print(
        f"итог: {files} файл(ов), {len(diff.splitlines())} строк,"
        f" {len(diff.encode('utf-8'))} байт — связность подтверждена"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
