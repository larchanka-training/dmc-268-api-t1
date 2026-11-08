"""Оценка стоимости прогона и цена токена (change `account-balance`, D4)."""

from decimal import Decimal

from app.domain.billing import covers, estimate_run_cost, eur_per_token


def test_price_is_cost_over_tokens() -> None:
    # Спека: 0.10 € за 100 000 токенов — 0.000001 € за токен.
    assert eur_per_token(Decimal("0.10"), 100_000) == Decimal("0.000001")


def test_price_without_history_is_zero() -> None:
    assert eur_per_token(Decimal(0), 0) == Decimal(0)


def test_estimate_counts_two_calls_of_prompt_and_output() -> None:
    # 2 × (1000 + 500) × 0.000001 = 0.003
    assert estimate_run_cost(1_000, 500, Decimal("0.000001")) == Decimal("0.003")


def test_estimate_keeps_decimal_precision() -> None:
    # 10 знаков, как у `cost_eur`: float на этих долях уже ошибся бы.
    price = eur_per_token(Decimal("0.0000913"), 913)

    assert price == Decimal("0.0000001")
    assert estimate_run_cost(3, 4, price) == Decimal("0.0000014")


def test_estimate_without_history_is_zero() -> None:
    assert estimate_run_cost(10_000, 4_096, Decimal(0)) == Decimal(0)


def test_balance_not_below_estimate_covers() -> None:
    assert covers(Decimal("0.06"), Decimal("0.05"))
    assert covers(Decimal("0.05"), Decimal("0.05"))
    assert not covers(Decimal("0.01"), Decimal("0.05"))


def test_zero_estimate_needs_positive_balance() -> None:
    assert covers(Decimal("0.01"), Decimal(0))
    assert not covers(Decimal(0), Decimal(0))
    assert not covers(Decimal("-0.5"), Decimal(0))
