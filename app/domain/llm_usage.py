"""Расход вызова модели: токены по видам и стоимость у провайдера.

Значение, а не счётчик: расход одного ответа провайдера, сумма ответов —
сложением. Стоимость — точный `Decimal` в евро, не float: из сумм складываются
списания (#286) и сверка с балансом провайдера (`/credits`, 10 знаков), а float
теряет доли на сложении. Цена фиксируется в момент вызова — расход несёт
стоимость, а не цену.
"""

from dataclasses import dataclass
from decimal import Decimal


@dataclass(frozen=True, slots=True)
class LlmUsage:
    """Расход ответа или суммы ответов.

    Кэшированные токены — часть входных, reasoning — часть выходных: так их
    считают провайдеры. `total_tokens` — итог провайдера (`usage.total_tokens`;
    без него адаптер берёт входные плюс выходные), прежнее единственное число
    `tokens_used`: сумма видов с ним может не совпасть.
    """

    input_tokens: int = 0
    output_tokens: int = 0
    cached_tokens: int = 0
    reasoning_tokens: int = 0
    total_tokens: int = 0
    cost_eur: Decimal = Decimal(0)

    def __add__(self, other: LlmUsage) -> LlmUsage:
        return LlmUsage(
            input_tokens=self.input_tokens + other.input_tokens,
            output_tokens=self.output_tokens + other.output_tokens,
            cached_tokens=self.cached_tokens + other.cached_tokens,
            reasoning_tokens=self.reasoning_tokens + other.reasoning_tokens,
            total_tokens=self.total_tokens + other.total_tokens,
            cost_eur=self.cost_eur + other.cost_eur,
        )


NO_USAGE = LlmUsage()
