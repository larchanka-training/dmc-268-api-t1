"""Адаптер `LlmGateway` к hosted-провайдеру по OpenAI-совместимому протоколу (Eurorouter).

`POST /chat/completions` с `response_format` по схеме модели ответа. Строгий
режим схемы выключен: он требует, чтобы каждое поле было обязательным, а у
моделей ответа есть поля со значением по умолчанию; ответ всё равно
проверяет наша модель в `complete_structured` (change `add-llm-gateway`, D1).

Ключей несколько (design D4). Каждый вызов начинает со следующего ключа по
кругу; ключ, отвергнутый провайдером или упёршийся в лимит (`401`, `403`,
`429`), сменяется следующим в том же вызове. Отвергнуты все - сбой провайдера;
если все по `429` - сбой «лимит запросов» (`LLM_RATE_LIMITED`). Ошибка сервера,
таймаут и обрыв соединения - сбой провайдера сразу (`LlmUnavailableError`, отказ
для breaker'а, #294): ключ тут ни при чём.

Ключи не попадают ни в текст ошибки, ни в лог: тексты строятся из кода
ответа и номера ключа, а описание ошибки провайдера очищается от ключей.
Каждый отказ по HTTP - запись в лог с кодом и `x-request-id` ответа (#119),
без текста ответа.

Расход - `usage` ответа целиком: входные, выходные, кэшированные и
reasoning-токены, итог провайдера (`total_tokens`) и стоимость. Eurorouter
отдаёт её в `usage.cost_eur` (и в `usage.cost` при `cost_currency: "EUR"`),
проверено на стенде 2026-11-06; у провайдера без стоимости в евро она 0
(change `run-token-cost`). Тело разбирается с `parse_float=Decimal`: число
из JSON сразу точное, через float стоимость не проходит.
"""

import itertools
import logging
import threading
from collections.abc import Mapping, Sequence
from decimal import ROUND_HALF_EVEN, Decimal, InvalidOperation
from typing import Any

import httpx
from pydantic import BaseModel, SecretStr

from app.application.ports.llm_gateway import LlmRequest, LlmResponse
from app.domain.entities import PromptMessage
from app.domain.llm_errors import (
    LlmError,
    LlmRateLimitedError,
    LlmTimeoutError,
    LlmUnavailableError,
)
from app.domain.llm_usage import LlmUsage
from app.infrastructure.llm.structured import RawReply, complete_structured, token_count
from app.infrastructure.telemetry import LLM_REQUEST_ID

logger = logging.getLogger(__name__)

# Отказ, за который отвечает ключ, а не провайдер: следующий ключ может пройти.
_KEY_REJECTED = frozenset({401, 403, 429})
# Стоимость хранится с 10 знаками после точки - столько отдаёт Eurorouter
# (`/generation`, `/credits`) - и меньше 10^10 € (колонка NUMERIC(20, 10)).
_COST_PLACES = Decimal("1E-10")
_COST_LIMIT = Decimal(10) ** 10


def _request_id(response: httpx.Response) -> dict[str, str]:
    """`x-request-id` отказа провайдера - полем записи лога: по нему поддержка Eurorouter находит запрос."""
    request_id = response.headers.get("x-request-id")
    return {LLM_REQUEST_ID: request_id} if request_id else {}


def _object(value: Any) -> Mapping[str, Any]:
    """Вложенный объект `usage` или пустой: учёт расхода не отвергает оплаченный ответ."""
    return value if isinstance(value, dict) else {}


class OpenAiCompatibleGateway:
    def __init__(
        self,
        client: httpx.Client,
        model: str,
        keys: Sequence[SecretStr],
        *,
        name: str,
        max_attempts: int,
        reasoning_effort: str | None = None,
    ) -> None:
        if not keys:
            raise ValueError(f"{name}: нужен хотя бы один ключ")
        self._client = client
        self._model = model
        self._keys = tuple(keys)
        self._name = name
        self._max_attempts = max_attempts
        self._reasoning_effort = reasoning_effort
        self._turns = itertools.count()
        self._lock = threading.Lock()

    def complete[T: BaseModel](self, request: LlmRequest[T]) -> LlmResponse[T]:
        with self._lock:
            first = next(self._turns) % len(self._keys)
        order = [(first + shift) % len(self._keys) for shift in range(len(self._keys))]
        response_format = {
            "type": "json_schema",
            "json_schema": {
                "name": request.response_model.__name__,
                "schema": request.response_model.model_json_schema(),
                "strict": False,
            },
        }

        def send(messages: Sequence[PromptMessage]) -> RawReply:
            body = {
                "model": self._model,
                "messages": [{"role": m.role.value, "content": m.content} for m in messages],
                "response_format": response_format,
                "max_tokens": request.max_output_tokens,
                # Воспроизводимый ответ: eval сравнивает прогоны на одном датасете.
                "temperature": 0,
            }
            # Только заданный: модель без параметра шлюз может отвергнуть с 400.
            if self._reasoning_effort is not None:
                body["reasoning_effort"] = self._reasoning_effort
            return self._post_rotating(body, order)

        return complete_structured(send, request, max_attempts=self._max_attempts)

    def _post_rotating(self, body: dict[str, Any], order: Sequence[int]) -> RawReply:
        rejected: list[int] = []
        for index in order:
            response = self._post(body, self._keys[index])
            if response.status_code in _KEY_REJECTED:
                rejected.append(response.status_code)
                logger.warning(
                    "%s: ключ %d из %d отвергнут (HTTP %d)",
                    self._name,
                    index + 1,
                    len(self._keys),
                    response.status_code,
                    extra=_request_id(response),
                )
                continue
            if response.status_code != httpx.codes.OK:
                logger.warning("%s: HTTP %d", self._name, response.status_code, extra=_request_id(response))
                # 5xx — отказ провайдера (breaker, #294); прочий 4xx — провайдер ответил.
                error_type = LlmUnavailableError if response.is_server_error else LlmError
                raise error_type(
                    f"{self._name}: HTTP {response.status_code}: {self._error_text(response)}"
                )
            return self._reply(response)
        codes = ", ".join(str(code) for code in rejected)
        if all(code == httpx.codes.TOO_MANY_REQUESTS for code in rejected):
            # Все ключи упёрлись в лимит: сбой временный, у него свой код.
            raise LlmRateLimitedError(f"{self._name}: лимит запросов на всех ключах (HTTP {codes})")
        raise LlmError(f"{self._name}: все ключи отвергнуты (HTTP {codes})")

    def _post(self, body: dict[str, Any], key: SecretStr) -> httpx.Response:
        try:
            return self._client.post(
                "/chat/completions",
                json=body,
                headers={"Authorization": f"Bearer {key.get_secret_value()}"},
            )
        except httpx.TimeoutException as error:
            raise LlmTimeoutError(f"{self._name}: таймаут ({type(error).__name__})") from None
        except httpx.RequestError as error:
            raise LlmUnavailableError(f"{self._name} недоступен ({type(error).__name__})") from None

    def _reply(self, response: httpx.Response) -> RawReply:
        unexpected = f"{self._name} вернул неожиданное тело на /chat/completions"
        try:
            # Из чисел тела адаптер читает только токены (целые) и стоимость:
            # дробные - `Decimal`, без float.
            data = response.json(parse_float=Decimal)
        except ValueError as error:
            raise LlmError(unexpected) from error
        if not isinstance(data, dict):
            raise LlmError(unexpected)
        # Расход разбирается до ответа и сам не падает: оплаченный ответ без
        # `choices` или `model` уходит сбоем уже с учтённым расходом, а не мимо
        # счётчика, и повтор или фоллбэк не прячут, что заплатили второй раз.
        tokens = self._usage(_object(data.get("usage")))
        try:
            choice = data["choices"][0]
            text: str = choice["message"]["content"]
            model: str = data["model"]
            # `length` - ответ оборван по `max_tokens`.
            truncated = choice.get("finish_reason") == "length"
        except (KeyError, IndexError, TypeError, AttributeError) as error:
            rejected = LlmError(unexpected)
            rejected.__cause__ = error
            return RawReply(text="", model=self._model, usage=tokens, rejected=rejected)
        return RawReply(text=text, model=model, usage=tokens, truncated=truncated)

    def _usage(self, usage: Mapping[str, Any]) -> LlmUsage:
        """Расход из `usage` ответа; кривое поле - 0 с предупреждением, а не сбой.

        Без `usage` или с не-объектом ответ всё равно принят: он оплачен, а
        отказ отправил бы повтор или фоллбэк и заплатил второй раз.
        """
        if all(usage.get(field) is None for field in ("prompt_tokens", "completion_tokens", "total_tokens")):
            # Ответ принят: он оплачен. Но без счётчиков расход учтён нулём,
            # и квота с бюджетом (#286) его не видят - об этом должно быть видно.
            logger.warning("%s: в ответе нет счётчиков токенов, расход учтён как 0", self._name)
        input_tokens = token_count(usage.get("prompt_tokens"))
        output_tokens = token_count(usage.get("completion_tokens"))
        total = usage.get("total_tokens")
        return LlmUsage(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cached_tokens=token_count(_object(usage.get("prompt_tokens_details")).get("cached_tokens")),
            reasoning_tokens=token_count(_object(usage.get("completion_tokens_details")).get("reasoning_tokens")),
            # Итог провайдера, как на develop; без него - сумма видов.
            total_tokens=input_tokens + output_tokens if total is None else token_count(total),
            cost_eur=self._cost_eur(usage),
        )

    def _cost_eur(self, usage: Mapping[str, Any]) -> Decimal:
        """Стоимость вызова в евро до 10 знаков; не в евро или нет - 0.

        Плохое значение (не число, `true`, NaN, отрицательное, не влезает в
        колонку) - предупреждение и 0, а не сбой: ответ модели уже оплачен,
        отвергнуть его из-за поля учёта значит заплатить повтором.
        """
        cost = usage.get("cost_eur")
        if cost is None and usage.get("cost_currency") == "EUR":
            cost = usage.get("cost")
        if cost is None:
            logger.warning("%s: в ответе нет стоимости в евро, учтена как 0", self._name)
            return Decimal(0)
        try:
            # `str`: число из тела уже `Decimal` или int, а `true`/NaN/Infinity
            # (их JSON отдаёт bool и float) отсюда не выходят числом. Лишние
            # знаки - банковским округлением (ROUND_HALF_EVEN) до 10-го знака.
            exact: Decimal | None = Decimal(str(cost)).quantize(_COST_PLACES, rounding=ROUND_HALF_EVEN)
        except InvalidOperation:
            exact = None
        if exact is None or not exact.is_finite() or exact < 0 or exact >= _COST_LIMIT:
            logger.warning("%s: стоимость вызова не разобрана, учтена как 0", self._name)
            return Decimal(0)
        return exact

    def _error_text(self, response: httpx.Response) -> str:
        """Описание ошибки провайдера без ключей: провайдер может процитировать ключ."""
        try:
            error = response.json().get("error")
            message = error.get("message") if isinstance(error, dict) else error
        except (ValueError, AttributeError):
            return "без описания"
        if not message:
            return "без описания"
        text = str(message)[:200]
        for key in self._keys:
            text = text.replace(key.get_secret_value(), "***")
        return text
