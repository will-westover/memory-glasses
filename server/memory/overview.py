"""Give a moment a title and a short overview from the summaries of its stretches."""

import argparse
import sys

from pydantic import BaseModel, Field

from memory import db, llm
from memory.config import SUMMARY_MODEL

INSTRUCTIONS = """\
You are given the summaries of one recording from someone's camera glasses, one per stretch of it, in order.

Write, using only what is given and in your own words:
- title: a few words naming the recording as a whole, the way a person would label it.
- overview: one or two plain sentences on what the recording was about."""


class Overview(BaseModel):
    title: str = Field(description="A few words naming the whole recording")
    overview: str = Field(description="One or two sentences on what the recording was about")


def write_overview(conn, moment_id: str) -> Overview | None:
    """Write and store a moment's title and overview from its stretch summaries."""
    summaries = db.summaries(conn, moment_id)

    if not summaries:
        return None

    numbered = "\n".join(f"{number}. {fact['text']}" for number, fact in enumerate(summaries, start=1))

    llm.require_price(SUMMARY_MODEL)
    response = llm.client().responses.parse(
        model=SUMMARY_MODEL,
        instructions=INSTRUCTIONS,
        input=numbered,
        text_format=Overview,
        reasoning={"effort": "low"},
    )
    llm.log_cost(conn, "overview", SUMMARY_MODEL, response.usage)

    written = response.output_parsed
    db.save_overview(conn, moment_id, written.title, written.overview)
    conn.commit()

    return written


def main() -> int:
    parser = argparse.ArgumentParser(description="Write the title and overview of moments.")
    parser.add_argument("moment_ids", nargs="*", help="moments to write (default: every moment without a title)")
    args = parser.parse_args()

    with db.connect() as conn:
        moment_ids = args.moment_ids or db.moments_without_title(conn)

        for moment_id in moment_ids:
            written = write_overview(conn, moment_id)
            print(f"{moment_id}: {written.title if written else 'no summaries'}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
