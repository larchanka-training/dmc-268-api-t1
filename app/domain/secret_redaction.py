"""Секреты в сохраняемом контексте прогона заменяются меткой.

Пакет контекста хранит дословно чужой код из PR, а вместе с ним — то, что автор
по ошибке закоммитил: ключи, токены. Модели пакет уходит как есть (ревью должно
увидеть зашитый секрет и сказать о нём), в базу — с метками вместо секретов.

shortcut: только формы с узнаваемым префиксом или рамкой — `password = "..."`
и случайные строки без префикса не ловятся, как и кусок ключа без BEGIN и END
(хунк задел только часть его строк); расширять, когда появится источник
ложных пропусков (eval или жалоба).
"""

import re
from typing import Any

REDACTED = "<секрет скрыт>"

# Начало токена с `-` внутри: начало пробега символов токена (не `task-sk-…`) или сразу
# после `-` удалённой строки диффа. Иначе поиск стартовал бы внутри пробега, и
# `--eyJ--eyJ…` сканировался бы квадратично.
_TOKEN_START = r"(?:(?<![\w-])|(?m:(?<=^-)))"
# Перед токеном без `-` внутри может стоять `_`: `GITHUB_TOKEN_ghp_…`, `key_AKIA…`.
# Для `sk-` так же, но не после `-`; JWT — только `_TOKEN_START`: после `_` пробег
# `_eyJ_eyJ…` без точек снова сканировался бы квадратично.
_AFTER_NAME = r"(?<![A-Za-z0-9])"
_SK_START = r"(?:(?<![A-Za-z0-9-])|(?m:(?<=^-)))"

_PATTERNS = (
    # Приватный ключ PEM или PGP; без END (хунк кончился) — заголовок и идущие за ним
    # строки base64. Заголовок в коде (`startswith("-----BEGIN…")`) без них не трогаем.
    (
        r"-----BEGIN[A-Z0-9 ]*PRIVATE KEY(?: BLOCK)?-----"
        r"(?:.*?-----END[A-Z0-9 ]*PRIVATE KEY(?: BLOCK)?-----|(?:\n[+ -]?[A-Za-z0-9+/=]+)+)"
    ),
    _AFTER_NAME + r"gh[pousr]_[A-Za-z0-9]{36,}",
    _AFTER_NAME + r"github_pat_[A-Za-z0-9_]{22,}",
    _AFTER_NAME + r"(?:AKIA|ASIA)[0-9A-Z]{16}\b",
    _AFTER_NAME + r"xox[abprs]-[A-Za-z0-9-]{10,}",
    _SK_START + r"sk-[A-Za-z0-9_-]{20,}",
    _AFTER_NAME + r"[sr]k_(?:live|test)_[A-Za-z0-9]{16,}",
    _AFTER_NAME + r"AIza[A-Za-z0-9_-]{35}",
    # Учётные данные в URL (`postgres://app:pass@db`) — только `user:pass`, хост остаётся.
    r"(?<=://)[^\s:/@]*:[^\s/@]+(?=@)",
    r"(?i:authorization)\W{1,4}Basic [A-Za-z0-9+/]+=*",
    _TOKEN_START + r"eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}",
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
