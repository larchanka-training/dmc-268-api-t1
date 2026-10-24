"""Доставки вебхука приложения на хостинге: список и переотправка (#149).

Структурный протокол, как и остальные порты: адаптер его не импортирует.
Отдельный от `VcsGateway`: это операции от имени приложения над его вебхуком,
а не над запросом на изменение. Сбой провайдера — `VcsError` из
`app.domain.vcs_errors`; лимит и сеть — его наследник `VcsUnavailableError`.
"""

from datetime import datetime
from typing import Protocol

from app.domain.webhook_redelivery import WebhookDelivery


class WebhookDeliveries(Protocol):
    def list_recent(self, since: datetime) -> list[WebhookDelivery]:
        """Попытки доставки не старше `since`, все — и успешные, и упавшие."""
        ...

    def delivery_payload(self, delivery_id: int) -> dict[str, object] | None:
        """Тело события, которое несла доставка; хостинг его не сохранил — `None`."""
        ...

    def redeliver(self, delivery_id: int) -> None:
        """Попросить хостинг переотправить доставку; ответ на неё придёт вебхуком."""
        ...
