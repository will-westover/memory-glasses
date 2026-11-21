"""OpenAI access shared by every step: one client, prices, and the cost log."""

import contextlib
import contextvars
import functools
import json

import psycopg
from openai import OpenAI
from psycopg.types.json import Jsonb

from memory import config
from memory.config import EMBED_MODEL, SERVER_DIR

PRICES_FILE = SERVER_DIR / "prices.json"
PRICES = json.loads(PRICES_FILE.read_text())["usd_per_million_tokens"]
COLLECTED: contextvars.ContextVar[list[int] | None] = contextvars.ContextVar("collected", default=None)


def require_price(model: str) -> None:
    """Stop before any call is paid for if the model has no price."""
    if model not in PRICES:
        raise SystemExit(f"error: no price for {model}. Add it to {PRICES_FILE}")


@functools.cache
def client() -> OpenAI:
    config.require("OPENAI_API_KEY")

    return OpenAI()


def cost_usd(model: str, input_tokens: int, cached_tokens: int, output_tokens: int) -> float:
    """Return what a call cost in dollars."""
    price = PRICES[model]
    fresh_tokens = input_tokens - cached_tokens

    dollars = fresh_tokens * price["input"] + cached_tokens * price["cached_input"] + output_tokens * price["output"]

    return dollars / 1_000_000


def log_cost(conn: psycopg.Connection, step: str, model: str, usage, frame_id: int | None = None,
             request: dict | None = None, response: dict | None = None) -> float:
    """Record one model call in model_call and return its cost in dollars."""
    input_tokens = getattr(usage, "input_tokens", None) or getattr(usage, "prompt_tokens", 0)
    details = getattr(usage, "input_tokens_details", None)
    cached_tokens = getattr(details, "cached_tokens", 0) or 0
    output_tokens = getattr(usage, "output_tokens", 0) or 0

    usd = cost_usd(model, input_tokens, cached_tokens, output_tokens)

    saved = conn.execute(
        "insert into model_call (step, model, frame_id, input_tokens, cached_tokens, output_tokens, usd, request,"
        " response) values (%s, %s, %s, %s, %s, %s, %s, %s, %s) returning id",
        (step, model, frame_id, input_tokens, cached_tokens, output_tokens, usd,
         Jsonb(request) if request is not None else None, Jsonb(response) if response is not None else None),
    ).fetchone()

    if saved is None:
        raise RuntimeError("the model call wasn't saved")

    call_id = saved[0]

    if (collected := COLLECTED.get()) is not None:
        collected.append(call_id)

    return usd


@contextlib.contextmanager
def collect():
    """Gather the ids of the model calls logged inside the block, in order."""
    ids: list[int] = []
    token = COLLECTED.set(ids)

    try:
        yield ids
    finally:
        COLLECTED.reset(token)


def embed(conn: psycopg.Connection, texts: list[str], step: str) -> list[str]:
    """Embed each text and return the vectors as text Postgres can store."""
    require_price(EMBED_MODEL)
    vectors = []

    for start in range(0, len(texts), 500):
        response = client().embeddings.create(model=EMBED_MODEL, input=texts[start : start + 500])
        log_cost(conn, step, EMBED_MODEL, response.usage)

        for item in response.data:
            vectors.append("[" + ",".join(map(str, item.embedding)) + "]")

    return vectors
