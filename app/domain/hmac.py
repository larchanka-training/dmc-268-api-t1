"""Проверка подписи вебхука GitHub — заголовка `X-Hub-Signature-256`.

URL вебхука знает любой, поэтому «наш ли это GitHub» решает подпись: функция
пересчитывает HMAC-SHA256 от сырого тела и сверяет его с присланным значением
в постоянном времени. Чистая функция над байтами — без настроек и сети,
эндпоинт отвечает 401 по ложному ответу, не разбирая причин.
"""

import hashlib
import hmac

_PREFIX = "sha256="


def verify_hmac(body: bytes, signature: str, secret: bytes) -> bool:
    """Вернуть `True`, если `signature` — HMAC-SHA256 от `body` ключом `secret`.

    Подпись приходит в формате GitHub `sha256=<hex>`. Всё, что не совпадает, —
    пустая строка, чужой префикс, битый hex, чужой секрет — ложь, а не
    исключение: вызывающему достаточно булевого ответа.
    """
    if not signature.startswith(_PREFIX):
        return False
    try:
        received = bytes.fromhex(signature[len(_PREFIX) :])
    except ValueError:
        return False
    expected = hmac.new(secret, body, hashlib.sha256).digest()
    return hmac.compare_digest(expected, received)
