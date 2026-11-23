"""Work out which stretch of time a question is about, before anything is searched."""

from datetime import datetime

from pydantic import BaseModel, Field

from server.shared import llm
from server.shared.config import AGENT_MODEL

INSTRUCTIONS = """\
You read one question someone asks about what their camera glasses recorded, and say which stretch of time it is
about, if any. It is now {now}.

If the question, or the conversation before it, points to a time ("on Friday", "this morning", "last week",
"after dinner yesterday", "on November 11"), give a window that surely contains it: a whole day for a day, a whole
morning, afternoon or evening for part of a day, a whole week for a week. Prefer too wide over too narrow.
If it points to no time ("what kind of tree is in my living room?", "who do I see most?"), say so and give no
window. Give times as local ISO 8601 without an offset, e.g. 2026-11-20T17:00:00."""


class Window(BaseModel):
    about_a_time: bool = Field(description="True if the question points to a time")
    start: str | None = Field(description="Start of the window, local ISO 8601, or null")
    end: str | None = Field(description="End of the window, local ISO 8601, or null")


def local_time(text: str | None) -> datetime | None:
    """Read a local ISO 8601 time, or return None if it can't be read."""
    try:
        at = datetime.fromisoformat(text) if text else None
    except ValueError:
        return None

    return at.astimezone() if at and not at.tzinfo else at


def question_window(conn, question: str, history: list[tuple[str, str]] = ()) -> tuple:
    """Return ((start, end), dollars): the time a question is about, or (None, None) if it names no time."""
    now = datetime.now().astimezone()
    earlier = "\n".join(f"Asked: {asked}\nAnswered: {answered}" for asked, answered in history)

    llm.require_price(AGENT_MODEL)
    response = llm.client().responses.parse(
        model=AGENT_MODEL,
        instructions=INSTRUCTIONS.format(now=f"{now:%A %d %B %Y %H:%M}"),
        input=f"{earlier}\n\nQuestion: {question}" if earlier else f"Question: {question}",
        text_format=Window,
        reasoning={"effort": "low"},
    )
    usd = llm.log_cost(conn, "when", AGENT_MODEL, response.usage)

    window = response.output_parsed
    start, end = (local_time(window.start), local_time(window.end)) if window and window.about_a_time else (None, None)

    return ((start, end) if start and end and start < end else (None, None)), usd
