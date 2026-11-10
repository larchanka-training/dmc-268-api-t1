"""Остаток баланса Eurorouter для метрики `review_llm_provider_balance_eur` (#119).

`GET /credits` отдаёт `data.total_credits` и `data.total_usage` строками в евро, остаток - их разность.
Колбэк метрики зовёт `EurorouterBalance()` на каждом экспорте, а к провайдеру запрос уходит не
чаще раза в `ttl_seconds`: между опросами отдаётся результат последнего. Сбой опроса (сеть, не
`200`, тело не того вида) - одно предупреждение без ключа и тела ответа, а остатка нет до
следующего опроса: ряд пропадает, а не держит старое значение. Ключ - первый из настроек.
"""

import logging
from collections.abc import Callable
from decimal import Decimal, InvalidOperation

import httpx
from pydantic import SecretStr

logger = logging.getLogger(__name__)


class EurorouterBalance:
    def __init__(
        self, client: httpx.Client, key: SecretStr, *, ttl_seconds: float, monotonic: Callable[[], float]
    ) -> None:
        self._client = client
        self._key = key
        self._ttl_seconds = ttl_seconds
        self._monotonic = monotonic
        self._checked_at: float | None = None
        self._balance: float | None = None

    def __call__(self) -> float | None:
        """Остаток в евро по последнему опросу; `None` - последний опрос не удался."""
        now = self._monotonic()
        if self._checked_at is None or now - self._checked_at >= self._ttl_seconds:
            # Время опроса - и при сбое: лежащий провайдер не опрашивается на каждом экспорте.
            self._checked_at = now
            self._balance = self._fetch()
        return self._balance

    def _fetch(self) -> float | None:
        try:
            response = self._client.get(
                "/credits", headers={"Authorization": f"Bearer {self._key.get_secret_value()}"}
            )
        except httpx.HTTPError as error:
            logger.warning("Eurorouter: баланс не получен (%s)", type(error).__name__)
            return None
        if response.status_code != httpx.codes.OK:
            logger.warning("Eurorouter: баланс не получен (HTTP %d)", response.status_code)
            return None
        try:
            data = response.json()["data"]
            return float(Decimal(data["total_credits"]) - Decimal(data["total_usage"]))
        except (ValueError, KeyError, TypeError, InvalidOperation):
            logger.warning("Eurorouter: баланс не разобран")
            return None
