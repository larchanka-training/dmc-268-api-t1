"""Секреты в сохраняемом контексте прогона заменяются меткой.

Пакет контекста хранит дословно чужой код из PR, а вместе с ним — то, что автор
по ошибке закоммитил: ключи, токены. Модели пакет уходит как есть (ревью должно
увидеть зашитый секрет и сказать о нём), в базу — с метками вместо секретов.

shortcut: только формы с узнаваемым префиксом или рамкой — `password = "..."`
и случайные строки без префикса не ловятся; расширять, когда появится
источник ложных пропусков (eval или жалоба).
"""

import re
from typing import Any

REDACTED = "<секрет скрыт>"

_PATTERNS = (
    # Приватный ключ PEM или PGP; без END (hunk обрезан) — до конца значения.
    (
        r"-----BEGIN[A-Z0-9 ]*PRIVATE KEY(?: BLOCK)?-----"
        r"(?:.*?-----END[A-Z0-9 ]*PRIVATE KEY(?: BLOCK)?-----|.*\Z)"
    ),
    r"\bgh[pousr]_[A-Za-z0-9]{36,}",
    r"\bgithub_pat_[A-Za-z0-9_]{22,}",
    r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b",
    r"\bxox[abprs]-[A-Za-z0-9-]{10,}",
    # Ключ модели; не после буквы или дефиса — `task-sk-…` внутри имени не секрет.
    r"(?<![\w-])sk-[A-Za-z0-9_-]{20,}",
    r"\b[sr]k_(?:live|test)_[A-Za-z0-9]{16,}",
    r"\bAIza[A-Za-z0-9_-]{35}",
    r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}",
)
_SECRETS = re.compile("|".join(_PATTERNS), re.DOTALL)


def redact_secrets(value: Any) -> Any:
    """Копия JSON-значения, в строках которого секреты заменены `REDACTED`."""
    if isinstance(value, str):
        return _SECRETS.sub(REDACTED, value)
    if isinstance(value, dict):
        return {key: redact_secrets(item) for key, item in value.items()}
    if isinstance(value, list):
        return [redact_secrets(item) for item in value]
    return value
