import pytest

from memory.llm import PRICES, cost_usd


def test_cost_adds_input_and_output_at_their_own_prices():
    input_price, _, output_price = PRICES["gpt-6-luna"]
    assert cost_usd("gpt-6-luna", 1_000_000, 0, 1_000_000) == pytest.approx(input_price + output_price)


def test_cached_tokens_are_part_of_input_and_cost_less():
    input_price, cached_price, _ = PRICES["gpt-6-luna"]
    cost = cost_usd("gpt-6-luna", 1_000_000, 400_000, 0)
    assert cost == pytest.approx(0.6 * input_price + 0.4 * cached_price)


def test_unknown_model_is_an_error_not_a_free_call():
    with pytest.raises(KeyError):
        cost_usd("no-such-model", 10, 0, 10)
