"""Access-лог uvicorn без query на любом пути.

uvicorn пишет путь вместе с query, а полезного в access-логе от query нет:
в query callback'а входа лежат `code` и `state`, в query остальных ручек —
поисковые и фильтровые параметры кабинета. Заголовков `Cookie` и `Set-Cookie`
access-лог не пишет, поэтому фильтр трогает только путь. Маркер общий со
скрытием query в серверном спане запроса на путях входа — его ставит фабрика
(`telemetry.redact_query`); спан там скрывается избирательно, лог — всегда.
"""

import logging

# Пути входа, где query скрыт ещё и в серверном спане (`telemetry.redact_query`).
AUTH_PREFIX = "/api/v1/auth/"
# Чем заменяется query: в логе — после `?`, в спане — значением `url.query`.
REDACTED = "<скрыто>"
# Формат uvicorn: '%s - "%s %s HTTP/%s" %d' — адрес, метод, путь, версия, статус.
_PATH_ARG = 2


class AccessQueryRedaction(logging.Filter):
    """Скрывает query пути в access-логе uvicorn — на любом пути, без исключений.

    Условие по префиксу путей входа убрано: query не нужен в access-логе нигде,
    а ограничение одним префиксом заставляло бы каждый новый путь с query
    вспоминать про этот фильтр.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        args = record.args
        if isinstance(args, tuple) and len(args) == 5:
            path = args[_PATH_ARG]
            if isinstance(path, str) and "?" in path:
                redacted = list(args)
                redacted[_PATH_ARG] = path.split("?", 1)[0] + "?" + REDACTED
                record.args = tuple(redacted)
        return True


# Один экземпляр: `addFilter` не добавляет тот же объект дважды, поэтому
# повторный `create_app` (в тестах) не плодит фильтры.
ACCESS_QUERY_REDACTION = AccessQueryRedaction()


def install() -> None:
    logging.getLogger("uvicorn.access").addFilter(ACCESS_QUERY_REDACTION)
