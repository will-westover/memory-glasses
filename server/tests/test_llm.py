import pytest

from memory.llm import PRICES, cost_usd, require_price


def test_cost_adds_input_and_output_at_their_own_prices():
    price = PRICES["gpt-6-luna"]
    assert cost_usd("gpt-6-luna", 1_000_000, 0, 1_000_000) == pytest.approx(price["input"] + price["output"])


def test_cached_tokens_are_part_of_input_and_cost_less():
    price = PRICES["gpt-6-luna"]
    cost = cost_usd("gpt-6-luna", 1_000_000, 400_000, 0)
    assert cost == pytest.approx(0.6 * price["input"] + 0.4 * price["cached_input"])


def test_unknown_model_is_stopped_before_any_call():
    with pytest.raises(SystemExit):
        require_price("no-such-model")
