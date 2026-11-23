"""What changed about things seen again: where each is now and how it looks, compared with the last time it was seen."""

from pydantic import BaseModel, Field

from server import db
from server.shared import llm
from server.shared.config import SUMMARY_MODEL

INSTRUCTIONS = """\
Below are things someone's camera glasses saw in one recording that had been seen before at the same place. For each
you get when it was last seen before and how it was then, and how it is now: the room, where it was, and what it
looked like. Most things will not have changed: say so by leaving them out.

Where a thing is was described from the camera's view (left, right, behind, in the foreground), which shifts whenever
the wearer stands somewhere else, and the same furniture can be named differently (a TV stand and a media console).
The room is the one the wearer stood in, and a thing can be seen from more than one room. Doors, windows, blinds,
built-in fixtures and large furniture do not move. So count a movable thing as moved only when it is clearly on or in a
different piece of furniture or surface than before (on the sofa before, on the bed now); being near, beside or on
the same furniture under another name is not a move. Count it as looking different only when its state clearly
changed (open or closed, on or off, folded, fuller or emptier), not when colours, names or details are just described
differently. Things worn or carried by the wearer move with them: they changed only if they were taken off, put down
or picked up. When in doubt, leave it out.

Write one short sentence for each real change, naming the thing plainly and saying when it was last seen, like "The
blanket was on the sofa on Monday; now it is on the bed."
"""


class Change(BaseModel):
    number: int = Field(description="The number of the thing that changed")
    text: str = Field(description="One short sentence on what changed since it was last seen")
    certain: bool = Field(description="True only if the before and now descriptions leave no doubt it really changed")


class Changes(BaseModel):
    changes: list[Change]


def described(sighting: dict) -> str:
    """Return one sighting as the model reads it."""
    room = f"in the {sighting['room']}, " if sighting["room"] else ""

    where, details = sighting["where"] or "somewhere", sighting["details"] or "no details"

    return f"{sighting['at'].astimezone():%A %d %B %H:%M}: {room}{where}; {details}"


def write_changes(conn, moment_id: str) -> int:
    """Compare each thing seen again in a moment with the last time it was seen, store what changed as facts, and
    return how many changes there were."""
    pairs = db.seen_again(conn, moment_id)
    found = []

    if pairs:
        llm.require_price(SUMMARY_MODEL)
        listed = "\n\n".join(
            f"{number}. {now['name']}\nBefore, {described(before)}\nNow, {described(now)}"
            for number, (now, before) in enumerate(pairs, start=1)
        )
        response = llm.client().responses.parse(
            model=SUMMARY_MODEL,
            instructions=INSTRUCTIONS,
            input=listed,
            text_format=Changes,
            reasoning={"effort": "medium"},
        )
        llm.log_cost(conn, "changes", SUMMARY_MODEL, response.usage)
        answered = response.output_parsed.changes if response.output_parsed else []
        found = [change for change in answered if change.certain and 0 < change.number <= len(pairs)]

    facts = [
        {
            "at": pairs[change.number - 1][0]["at"],
            "offset_seconds": pairs[change.number - 1][0]["offset_seconds"],
            "end_offset_seconds": pairs[change.number - 1][0]["offset_seconds"],
            "text": change.text,
            "frame_ids": [pairs[change.number - 1][0]["frame_id"]],
            "entity_id": pairs[change.number - 1][0]["entity_id"],
        }
        for change in found
    ]
    vectors = llm.embed(conn, [fact["text"] for fact in facts], "embed") if facts else []
    db.add_facts(conn, moment_id, "change", facts, vectors)
    conn.commit()

    return len(facts)
