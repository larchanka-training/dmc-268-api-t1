"""Экспорт схемы OpenAPI из кода в `docs/openapi/openapi.json`.

Источник правды — код (`app/api`); файл — его снимок для фронтенда (генерация типов) и
ревью. Руками файл не правится.

    uv run python scripts/export_openapi.py           # записать файл
    uv run python scripts/export_openapi.py --check   # сверить; rc=1, если файл устарел

Приложение собирается без внешних сервисов: адреса базы и брокера в настройках
фиктивные, соединений при сборке нет.
"""

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OPENAPI_JSON = ROOT / "docs" / "openapi" / "openapi.json"
STALE_MESSAGE = "контракт устарел: запустите uv run python scripts/export_openapi.py"


def render() -> str:
    """Схема приложения как текст файла: стабильный порядок ключей, перевод строки в конце."""
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    from app.api.factory import create_app
    from app.config import Settings

    settings = Settings(
        database_url="postgresql+psycopg://export:export@localhost/export",
        rabbitmq_url="amqp://guest:guest@localhost//",
        github_webhook_secret="export",
        public_origin="https://review.example",
    )
    schema = create_app(settings).openapi()
    return json.dumps(schema, ensure_ascii=False, indent=2, sort_keys=False) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Экспорт схемы OpenAPI в docs/openapi/openapi.json.")
    parser.add_argument("--check", action="store_true", help="не писать файл, а сверить его со схемой из кода")
    args = parser.parse_args(argv)

    text = render()
    if args.check:
        current = OPENAPI_JSON.read_text(encoding="utf-8") if OPENAPI_JSON.exists() else None
        if current != text:
            print(f"{OPENAPI_JSON.relative_to(ROOT)}: {STALE_MESSAGE}", file=sys.stderr)
            return 1
        return 0
    current = OPENAPI_JSON.read_text(encoding="utf-8") if OPENAPI_JSON.exists() else None
    if current != text:
        OPENAPI_JSON.write_text(text, encoding="utf-8")
        print(f"{OPENAPI_JSON.relative_to(ROOT)} обновлён — добавьте его в коммит")
    return 0


if __name__ == "__main__":
    sys.exit(main())
