"""Access-лог uvicorn без query на путях входа.

uvicorn пишет путь вместе с query, а в query callback'а лежат `code` и
`state`. Заголовков `Cookie` и `Set-Cookie` access-лог не пишет, поэтому
фильтр трогает только путь. Префикс и маркер общие со скрытием query в
серверном спане запроса — его ставит фабрика (`telemetry.redact_query`).
"""

import logging

AUTH_PREFIX = "/api/v1/auth/"
# Чем заменяется query: в логе — после `?`, в спане — значением `url.query`.
REDACTED = "<скрыто>"
# Формат uvicorn: '%s - "%s %s HTTP/%s" %d' — адрес, метод, путь, версия, статус.
_PATH_ARG = 2


class AuthQueryRedaction(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        args = record.args
        if isinstance(args, tuple) and len(args) == 5:
            path = args[_PATH_ARG]
            if isinstance(path, str) and path.startswith(AUTH_PREFIX) and "?" in path:
                redacted = list(args)
                redacted[_PATH_ARG] = path.split("?", 1)[0] + "?" + REDACTED
                record.args = tuple(redacted)
        return True


# Один экземпляр: `addFilter` не добавляет тот же объект дважды, поэтому
# повторный `create_app` (в тестах) не плодит фильтры.
AUTH_QUERY_REDACTION = AuthQueryRedaction()


def install() -> None:
    logging.getLogger("uvicorn.access").addFilter(AUTH_QUERY_REDACTION)
