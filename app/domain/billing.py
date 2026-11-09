"""Оценка стоимости прогона и решение, хватает ли на неё баланса (change `account-balance`).

Деньги — точный `Decimal` в евро, как стоимость прогона (`LlmUsage.cost_eur`).
"""

from decimal import Decimal

# Вызовов модели два — ревью и верификация, — и промпт верификации не больше промпта ревью.
# Повторы при сбоях не закладываются (design D4).
_CALLS = 2


def eur_per_token(cost_eur: Decimal, tokens: int) -> Decimal:
    """Цена токена по истории прогонов: их стоимость на их токены; без истории — ноль."""
    if tokens <= 0:
        return Decimal(0)
    return cost_eur / tokens


def estimate_run_cost(prompt_tokens: int, max_output_tokens: int, price: Decimal) -> Decimal:
    """Оценка прогона сверху: два вызова, каждый — промпт ревью и ответ на пределе."""
    return price * _CALLS * (prompt_tokens + max_output_tokens)


def covers(balance: Decimal, estimate: Decimal) -> bool:
    """Хватает ли баланса на прогон: не меньше оценки и, при нулевой оценке, больше нуля."""
    return balance > 0 and balance >= estimate
