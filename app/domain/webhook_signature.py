"""Проверка подлинности вебхука GitHub.

Чистое решение над данными: секрет, сырое тело и значение заголовка
`X-Hub-Signature-256` приходят аргументами, ничего не читается из окружения.
Подлинность вебхука — свойство безопасности (`docs/SYSTEM_DESIGN.md` §4.1):
без неё любой, кто узнал endpoint, заставит систему ревьюить что угодно.
"""

import hashlib
import hmac

_PREFIX = "sha256="


def verify_webhook_signature(secret: str, body: bytes, header_value: str | None) -> bool:
    if not header_value or not header_value.startswith(_PREFIX):
        return False
    expected = _PREFIX + hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    # Сравнение байтов: `compare_digest` на `str` бросает `TypeError` при
    # символе вне ASCII, а Starlette декодирует заголовки как latin-1, то есть
    # такой символ приходит снаружи — и превращал бы 401 в 500.
    return hmac.compare_digest(expected.encode("utf-8"), header_value.encode("utf-8"))
