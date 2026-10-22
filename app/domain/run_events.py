"""Признак жизни потока событий: `ping` в тишине.

Когда его отправить, решает цикл общего потока в
`app/application/run_feed_events.py`, запись в текст SSE — `app/api/events.py`.
"""

from dataclasses import dataclass

# Обещано не реже раза в 15 секунд (клиент считает поток мёртвым после 45).
# Секунда запаса — на шаг опроса и чтение базы: тишина меряется шагами цикла.
PING_AFTER_SECONDS = 14.0


@dataclass(frozen=True, slots=True)
class PingEvent:
    """Признак жизни потока; данных не несёт."""
