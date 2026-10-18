"""Правила входа, которые не зависят от провайдера.

`redirect` приходит от клиента дважды — в запросе входа и в cookie
`oauth_state`, — поэтому проверяется функцией, которую зовут оба места.
Принимается только относительный путь под `/app/`: остальное — открытый
редирект или выход из приложения, и вход тогда ведёт на `/app/`.
"""

from enum import StrEnum
from urllib.parse import unquote

DEFAULT_REDIRECT = "/app/"

# `redirect` едет в cookie `oauth_state` в процент-кодировке: до трёх символов на
# байт. Браузер отбрасывает cookie больше 4096 байт, и вход тогда кончается
# `state_mismatch`, поэтому длинный адрес заменяется на `/app/` заранее.
MAX_REDIRECT_BYTES = 1000


class LoginError(StrEnum):
    """Код ошибки в `/login?error=…`."""

    STATE_MISMATCH = "state_mismatch"
    DENIED = "denied"
    PROVIDER_UNAVAILABLE = "provider_unavailable"


class OAuthProviderUnavailable(RuntimeError):
    """Обмен кода или запрос профиля у провайдера не удался.

    Здесь, а не в порту: адаптер её бросает, use case ловит, и ни один не
    импортирует другого.
    """


def safe_redirect(value: str | None) -> str:
    """`value`, если это путь под `/app/`, иначе `/app/`."""
    if not value or len(value.encode()) > MAX_REDIRECT_BYTES:
        return DEFAULT_REDIRECT
    # Браузер раскрывает `%2e` в точку, поэтому проверяется и раскрытый вид.
    for form in (value, unquote(value)):
        if not form.startswith(DEFAULT_REDIRECT):
            return DEFAULT_REDIRECT
        if "//" in form or "\\" in form or any(ord(ch) < 0x20 for ch in form):
            return DEFAULT_REDIRECT
        path = form.split("?", 1)[0].split("#", 1)[0]
        if any(segment in (".", "..") for segment in path.split("/")):
            return DEFAULT_REDIRECT
    return value
