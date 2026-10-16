"""Оценка токенов и бюджет — точные значения на литералах."""

from app.domain.context_budget import Budget, estimate_tokens, json_size, serialize


def test_serialization_is_compact_sorted_and_keeps_unicode() -> None:
    assert serialize({"b": 1, "a": "я"}) == '{"a":"я","b":1}'


def test_estimate_tokens_rounds_bytes_up() -> None:
    assert estimate_tokens("") == 0
    assert estimate_tokens("abcd") == 1
    assert estimate_tokens("abcde") == 2
    # Кириллица — два байта на символ: два символа — один токен.
    assert estimate_tokens("яя") == 1


def test_item_cost_is_json_plus_separator() -> None:
    assert Budget.cost({"a": 1}) == len('{"a":1}') + 1


def test_take_refuses_what_does_not_fit_and_charges_nothing() -> None:
    budget = Budget(10)
    assert budget.take("abcdef")  # "abcdef" в кавычках + запятая = 9
    assert budget.remaining == 1
    assert not budget.take("x")
    assert budget.remaining == 1


def test_items_paid_through_budget_never_exceed_limit() -> None:
    """Каркас + элементы, оплаченные бюджетом, укладываются в лимит."""
    limit = 200
    skeleton: dict[str, list[object]] = {"items": []}
    budget = Budget(limit - json_size(skeleton))
    items = []
    for n in range(100):
        item = {"n": n, "text": "x" * (n % 7)}
        if budget.take(item):
            items.append(item)
    assert json_size({"items": items}) <= limit
    assert items  # что-то влезло


def test_split_and_merge_return_unused_share() -> None:
    budget = Budget(100)
    share = budget.split(30)
    assert (budget.remaining, share.remaining) == (70, 30)
    share.take("abc")  # 6 байт
    budget.merge(share)
    assert budget.remaining == 94
    assert share.remaining == 0


def test_split_cannot_exceed_remaining() -> None:
    budget = Budget(10)
    share = budget.split(30)
    assert (budget.remaining, share.remaining) == (0, 10)
