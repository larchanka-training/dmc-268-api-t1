"""Расход вызова модели: сумма по видам токенов и точной стоимости."""

from decimal import Decimal

from app.domain.llm_usage import NO_USAGE, LlmUsage


def test_usage_adds_every_kind_total_and_cost() -> None:
    first = LlmUsage(
        input_tokens=30,
        output_tokens=5,
        cached_tokens=10,
        reasoning_tokens=2,
        total_tokens=35,
        cost_eur=Decimal("0.0000913"),
    )
    second = LlmUsage(input_tokens=100, output_tokens=20, total_tokens=121, cost_eur=Decimal("0.0000087"))

    assert first + second == LlmUsage(
        input_tokens=130,
        output_tokens=25,
        cached_tokens=10,
        reasoning_tokens=2,
        total_tokens=156,
        cost_eur=Decimal("0.0001"),
    )


def test_cost_of_many_calls_sums_exactly() -> None:
    # Сумма десяти float 0.1 - не 1.0; у Decimal - ровно 1.
    call = LlmUsage(cost_eur=Decimal("0.1"))
    total = NO_USAGE
    for _ in range(10):
        total += call

    assert total.cost_eur == Decimal(1)


def test_no_usage_is_zero() -> None:
    assert NO_USAGE == LlmUsage(0, 0, 0, 0, 0, Decimal(0))
    assert NO_USAGE + LlmUsage(input_tokens=1, total_tokens=1) == LlmUsage(input_tokens=1, total_tokens=1)
