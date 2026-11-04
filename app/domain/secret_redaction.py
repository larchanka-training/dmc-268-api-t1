"""Секреты в сохраняемом контексте прогона заменяются меткой.

Пакет контекста хранит дословно чужой код из PR, а вместе с ним — то, что автор
по ошибке закоммитил: ключи, токены. Модели пакет уходит как есть (ревью должно
увидеть зашитый секрет и сказать о нём), в базу — с метками вместо секретов.

Метка ставится на каждую строку, которую занимал секрет (многострочный ключ, имя и
значение на соседних строках): строки диффа и окна вокруг него не сдвигаются, у строк
диффа остаётся префикс `+`/`-`/пробел (change `context-surrounding`, design D6).

Id блоков недоверенных данных в заменённой строке пересчитывается: прежний —
префикс sha256 текста с секретом, и по нему слабый пароль подбирается офлайн.

shortcut: только формы с узнаваемым префиксом, именем или рамкой — `password = "..."`
и случайные строки без префикса не ловятся, как и середина ключа без BEGIN и END
(хунк задел только часть его строк), а однословная строка кода прямо над END без
пары заменяется меткой вместе с хвостом ключа; расширять, когда появится источник
ложных пропусков (eval или жалоба).
"""

import re
from fnmatch import fnmatchcase
from posixpath import basename
from typing import Any

from app.domain.code_context import language_for_path
from app.domain.untrusted import retag

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

_END = r"-----END[A-Z0-9 ]*PRIVATE KEY(?: BLOCK)?-----"
# Строка ключа в base64: префикс строки диффа и отступ (YAML `key: |`, `'''`) допустимы.
_KEY_LINE = re.compile(r"[+ -]?[ \t]*[A-Za-z0-9+/=]+[ \t]*")

_PATTERNS = (
    # Приватный ключ PEM или PGP: до END, но не дальше следующего BEGIN — иначе каждый BEGIN
    # без END пробегал бы до конца строки и поиск стал бы квадратичным. Без END (хунк
    # кончился) — заголовок и строки base64 за ним; заголовок в коде без них не трогаем.
    (
        r"-----BEGIN[A-Z0-9 ]*PRIVATE KEY(?: BLOCK)?-----"
        r"(?:(?:(?!-----BEGIN).)*?" + _END + r"|(?:\n[+ -]?[ \t]*[A-Za-z0-9+/=]+[ \t]*(?=\n|\Z))+)"
    ),
    _AFTER_NAME + r"gh[pousr]_[A-Za-z0-9]{36,}",
    _AFTER_NAME + r"github_pat_[A-Za-z0-9_]{22,}",
    _AFTER_NAME + r"(?:AKIA|ASIA)[0-9A-Z]{16}\b",
    _AFTER_NAME + r"xox[abprs]-[A-Za-z0-9-]{10,}",
    _SK_START + r"sk-[A-Za-z0-9_-]{20,}",
    _AFTER_NAME + r"[sr]k_(?:live|test)_[A-Za-z0-9]{16,}",
    _AFTER_NAME + r"AIza[A-Za-z0-9_-]{35}",
    _AFTER_NAME + r"glpat-[A-Za-z0-9_-]{20,}",
    _AFTER_NAME + r"npm_[A-Za-z0-9]{36,}",
    _AFTER_NAME + r"pypi-AgEIcHlwaS5vcmc[A-Za-z0-9_-]{50,}",
    _AFTER_NAME + r"hf_[A-Za-z0-9]{34,}",
    r"https://hooks\.slack\.com/services/[A-Za-z0-9/]+",
    # Учётные данные в URL (`postgres://app:pass@db`) — только `user:pass`, хост остаётся.
    r"(?<=://)[^\s:/@]*:[^\s/]+(?=@)",
    # Схема HTTP-авторизации регистронезависима.
    r"(?i:authorization\W{1,4}(?:basic|bearer)) [A-Za-z0-9._~+/-]+=*",
    # Сам секрет AWS — в паре с `AKIA…` по имени, префикса у него нет.
    r"(?i:aws_?secret_?access_?key)\W{1,4}[A-Za-z0-9/+=]{40}",
    _TOKEN_START + r"eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}",
)
_SECRETS = re.compile("|".join(_PATTERNS), re.DOTALL)
_ORPHAN_END = re.compile(_END)


# Имена файлов, где секрет без узнаваемого префикса - обычное дело: `DB_PASSWORD=hunter2`,
# голый ключ. Окно и файл целиком им не положены, модель видит только hunk'и.
_SECRET_FILE_NAMES = (".env*", "*.pem", "*.key", "credentials*")


def may_hold_secrets(path: str) -> bool:
    """Не код, а файл, похожий на хранилище секретов: `.env`, ключ, credentials или `secret`
    в пути (`k8s/secrets/db.yaml`). Код о секретах (`secret_redaction.py`) ревьюится с окном."""
    if language_for_path(path) is not None:
        return False
    lowered = path.lower()
    name = basename(lowered)
    return "secret" in lowered or any(fnmatchcase(name, p) for p in _SECRET_FILE_NAMES)


def redact_secrets(value: Any) -> Any:
    """Копия JSON-значения, в строках которого секреты заменены `REDACTED`."""
    if isinstance(value, str):
        redacted = _redact_key_tails(_SECRETS.sub(_label, value))
        return value if redacted == value else retag(redacted)
    if isinstance(value, dict):
        return {key: redact_secrets(item) for key, item in value.items()}
    if isinstance(value, list):
        return [redact_secrets(item) for item in value]
    return value


def _label(match: re.Match[str]) -> str:
    """Метка на каждую строку совпадения: номера строк диффа и вида не сдвигаются.

    Префикс строки диффа (`+`, `-`, пробел) у второй и следующих строк остаётся,
    чтобы hunk оставался hunk'ом.
    """
    _, *rest = match[0].split("\n")
    return "\n".join([REDACTED, *(_line_label(line) for line in rest)])


def _line_label(line: str) -> str:
    return (line[0] if line[:1] in ("+", "-", " ") else "") + REDACTED


def _redact_key_tails(text: str) -> str:
    """END без BEGIN (хунк начался внутри ключа): END и строки base64 перед ним.

    Хвост — не огрызок: у ed25519 две последние строки base64 уже содержат весь seed.
    Пары BEGIN…END к этому моменту заменены, остались только END без BEGIN. Проход по
    строкам, а не регулярка назад от END: каждая строка кладётся и снимается один раз.
    """
    if _ORPHAN_END.search(text) is None:
        return text
    kept: list[str] = []
    for line in text.split("\n"):
        if _ORPHAN_END.search(line):
            # Строки ключа - метками на своих местах, а не удаляются: номера строк не сдвигаются.
            index = len(kept)
            while index and _KEY_LINE.fullmatch(kept[index - 1]):
                index -= 1
                kept[index] = _line_label(kept[index])
            line = _ORPHAN_END.sub(REDACTED, line)
        kept.append(line)
    return "\n".join(kept)
