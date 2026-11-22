"""What model calls have cost."""

import psycopg


def step_cost(conn: psycopg.Connection, step: str) -> float:
    """Return the total dollars logged for one pipeline step."""
    (usd,) = conn.execute("select coalesce(sum(usd), 0) from model_call where step = %s", (step,)).fetchone()

    return float(usd)


def costs(conn: psycopg.Connection) -> list[dict]:
    """Return the calls, tokens and dollars logged for each pipeline step."""
    rows = conn.execute(
        "select step, count(*), sum(input_tokens), sum(output_tokens), sum(usd) from model_call"
        " group by step order by sum(usd) desc"
    )

    return [
        {"step": step, "calls": calls, "input_tokens": tokens_in, "output_tokens": tokens_out, "usd": float(usd)}
        for step, calls, tokens_in, tokens_out, usd in rows
    ]


def average_caption_cost(conn: psycopg.Connection) -> float:
    """Return the average cost of describing one frame so far."""
    (average,) = conn.execute("select coalesce(avg(usd), 0.006) from model_call where step = 'caption'").fetchone()

    return float(average)
