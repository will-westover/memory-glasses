"""OpenAI access shared by every step: one client, prices, and the cost log."""

import functools

import psycopg
from openai import OpenAI

from memory import config

# US dollars per 1 million tokens: (input, cached input, output).
# From https://developers.openai.com/api/docs/pricing on 2026-11-11.
PRICES = {
    "gpt-6-luna": (0.10, 0.01, 0.50),
    "gpt-6-sol": (2.00, 0.20, 10.00),
    "gpt-5.6-luna": (0.20, 0.02, 1.20),
    "gpt-5.6-terra": (2.00, 0.20, 12.00),
    "gpt-5.6-sol": (4.00, 0.40, 20.00),
    "text-embedding-3-small": (0.02, 0.02, 0.0),
}


@functools.cache
def client() -> OpenAI:
    config.require("OPENAI_API_KEY")

    return OpenAI()


def cost_usd(model: str, input_tokens: int, cached_tokens: int, output_tokens: int) -> float:
    """Return what a call cost. cached_tokens is the part of input_tokens billed at the cheaper cached rate."""
    input_price, cached_price, output_price = PRICES[model]
    fresh_tokens = input_tokens - cached_tokens

    return (fresh_tokens * input_price + cached_tokens * cached_price + output_tokens * output_price) / 1_000_000


def log_cost(conn: psycopg.Connection, step: str, model: str, usage, frame_id: int | None = None) -> float:
    """Record one model call in api_cost and return its cost in dollars. `usage` is the response's usage."""
    details = getattr(usage, "input_tokens_details", None)
    cached_tokens = getattr(details, "cached_tokens", 0) or 0
    output_tokens = getattr(usage, "output_tokens", 0) or 0

    usd = cost_usd(model, usage.input_tokens, cached_tokens, output_tokens)

    conn.execute(
        "insert into api_cost (step, model, frame_id, input_tokens, cached_tokens, output_tokens, usd)"
        " values (%s, %s, %s, %s, %s, %s, %s)",
        (step, model, frame_id, usage.input_tokens, cached_tokens, output_tokens, usd),
    )

    return usd
