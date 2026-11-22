"""Give a moment a title and a short overview, and each of its minute summaries a chapter title."""

import argparse
import sys

from pydantic import BaseModel, Field

from memory import db, llm
from memory.config import SUMMARY_MODEL

INSTRUCTIONS = """\
You are given the minute-by-minute summaries of one recording from someone's camera glasses, numbered in order.

Write, using only what is given and in your own words:
- title: a few words naming the recording as a whole, the way a person would label it.
- overview: one or two plain sentences on what the recording was about.
- chapters: for each numbered summary, a title of two to five words saying what that minute was about,
  so someone scanning the list can tell the minutes apart."""


class Chapter(BaseModel):
    number: int = Field(description="The number of the summary this title is for")
    title: str = Field(description="Two to five words on what that minute was about")


class Overview(BaseModel):
    title: str = Field(description="A few words naming the whole recording")
    overview: str = Field(description="One or two sentences on what the recording was about")
    chapters: list[Chapter] = Field(description="One title per numbered summary")


def write_overview(conn, moment_id: str) -> Overview | None:
    """Write and store a moment's title, overview and chapter titles from its minute summaries."""
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
    titles = {
        summaries[chapter.number - 1]["id"]: chapter.title
        for chapter in written.chapters
        if 0 < chapter.number <= len(summaries)
    }

    db.save_overview(conn, moment_id, written.title, written.overview, titles)
    conn.commit()

    return written


def main() -> int:
    parser = argparse.ArgumentParser(description="Write the title, overview and chapter titles of moments.")
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
