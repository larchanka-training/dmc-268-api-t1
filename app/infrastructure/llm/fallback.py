"""Составной `LlmGateway`: провайдеры по порядку, первый ответ побеждает.

Порядок собирает composition root, и выбора там нет: локальная модель
первая, hosted-провайдер - только вторым и только при явном включении
(change `add-llm-gateway`, design D3). Здесь лишь применяется порядок.

Сбой модели любого класса (`LlmError`, включая `LlmOutputError`) передаёт тот
же запрос следующему провайдеру; после последнего пробрасывается его сбой.
Прочие исключения - ошибки программы - не гасятся и дальше не передаются.
Токены, которые сожгли сбойные провайдеры, прибавляются к ответу или к
последнему сбою: прогон учитывает весь расход вызова.
"""

import logging
from collections.abc import Sequence
from dataclasses import replace

from pydantic import BaseModel

from app.application.ports.llm_gateway import LlmGateway, LlmRequest, LlmResponse
from app.domain.llm_errors import LlmError

logger = logging.getLogger(__name__)


class FallbackLlmGateway:
    def __init__(self, providers: Sequence[tuple[str, LlmGateway]]) -> None:
        if not providers:
            raise ValueError("нужен хотя бы один провайдер модели")
        self._providers = tuple(providers)

    def complete[T: BaseModel](self, request: LlmRequest[T]) -> LlmResponse[T]:
        *heads, (_, last) = self._providers
        spent = 0
        for (name, provider), (next_name, _) in zip(heads, self._providers[1:], strict=True):
            try:
                return _plus(provider.complete(request), spent)
            except LlmError as error:
                spent += error.tokens_used
                logger.warning(
                    "модель %s не ответила на %s (%s: %s), запрос уходит %s",
                    name,
                    request.task,
                    type(error).__name__,
                    error,
                    next_name,
                )
        try:
            return _plus(last.complete(request), spent)
        except LlmError as error:
            error.tokens_used += spent
            raise


def _plus[T: BaseModel](response: LlmResponse[T], spent: int) -> LlmResponse[T]:
    return replace(response, tokens_used=response.tokens_used + spent) if spent else response
