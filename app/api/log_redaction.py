"""Access-лог uvicorn без query на путях входа.

uvicorn пишет путь вместе с query, а в query callback'а лежат `code` и
`state`. Заголовков `Cookie` и `Set-Cookie` access-лог не пишет, поэтому
фильтр трогает только путь.
"""

import logging

_AUTH_PREFIX = "/api/v1/auth/"
# Формат uvicorn: '%s - "%s %s HTTP/%s" %d' — адрес, метод, путь, версия, статус.
_PATH_ARG = 2


class AuthQueryRedaction(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        args = record.args
        if isinstance(args, tuple) and len(args) == 5:
            path = args[_PATH_ARG]
            if isinstance(path, str) and path.startswith(_AUTH_PREFIX) and "?" in path:
                redacted = list(args)
                redacted[_PATH_ARG] = path.split("?", 1)[0] + "?<скрыто>"
                record.args = tuple(redacted)
        return True


# Один экземпляр: `addFilter` не добавляет тот же объект дважды, поэтому
# повторный `create_app` (в тестах) не плодит фильтры.
AUTH_QUERY_REDACTION = AuthQueryRedaction()


def install() -> None:
    logging.getLogger("uvicorn.access").addFilter(AUTH_QUERY_REDACTION)
