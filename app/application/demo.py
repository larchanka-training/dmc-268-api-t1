"""Решение демо-режима (change `demo-mode`): подставлять ли демо-пользователя вместо `401`."""

# Демо — только чтение: изменяющий запрос без сессии получает `401`, как вне демо-режима.
_READ_METHODS = frozenset({"GET", "HEAD"})


def demo_fallback(demo_mode: bool, method: str, has_session: bool) -> bool:
    """Обслужить запрос как запрос демо-пользователя.

    Только при включённом демо-режиме, только читающий запрос и только без действительной
    сессии: вошедший пользователь всегда остаётся собой.
    """
    return demo_mode and not has_session and method.upper() in _READ_METHODS
