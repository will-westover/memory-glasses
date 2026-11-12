"""Turn a session's captions and transcript into small facts, and embed each one with its context.

    uv run python -m memory.ingest.facts <session id>

A fact is one searchable thing: a moment, an object and where it was, a piece of readable text, or a passage of
speech. Each carries where and when it happened, so it still makes sense when read alone:

    keys: on kitchen counter. Home, kitchen, Tuesday evening. A person is making coffee.

Run it after the captions and transcript steps. It rebuilds every fact for the session each time.
"""

import argparse
import sys
from datetime import datetime

from memory import db, llm
from memory.places import place_for

# Transcript lines are joined into passages of up to this many words, so short lines aren't embedded alone.
PASSAGE_WORDS = 60

# A pause this long between lines starts a new passage.
PASSAGE_GAP_SECONDS = 8.0


def part_of_day(at: datetime) -> str:
    """Return the weekday and part of the day in local time, e.g. 'Tuesday evening'."""
    local = at.astimezone()

    if 5 <= local.hour < 12:
        part = "morning"
    elif 12 <= local.hour < 17:
        part = "afternoon"
    elif 17 <= local.hour < 22:
        part = "evening"
    else:
        part = "night"

    return f"{local:%A} {part}"


def context(at: datetime, place: str | None, setting: str | None) -> str:
    """Return where and when as a short phrase, e.g. 'Home, kitchen, Tuesday evening'."""
    parts = [place, setting if setting != "other" else None, part_of_day(at)]

    return ", ".join(part for part in parts if part)


def first_sentence(text: str) -> str:
    """Return the text up to its first full stop."""
    head, stop, _ = text.partition(". ")

    return head + "." if stop else text


def frame_facts(frames: list[dict], place: str | None) -> list[dict]:
    """Return a moment fact for each frame, and a fact for each object and piece of text in it.

    An object or text that is described identically in consecutive frames gives one fact, at its first frame.
    """
    facts = []
    previous_objects: set = set()
    previous_texts: set = set()

    for frame in frames:
        where_when = context(frame["at"], place, frame["setting"])
        scene = first_sentence(frame["summary"])
        base = {"frame_id": frame["id"], "at": frame["at"], "offset_seconds": frame["offset_seconds"]}

        facts.append({**base, "kind": "moment", "text": f"{frame['summary']} {where_when}."})

        for name, relation, anchor in frame["objects"]:
            if (name, relation, anchor) not in previous_objects:
                facts.append({**base, "kind": "object", "text": f"{name}: {relation} {anchor}. {where_when}. {scene}"})

        for text, surface in frame["texts"]:
            if (text, surface) not in previous_texts:
                facts.append({**base, "kind": "text", "text": f'Text on {surface}: "{text}". {where_when}. {scene}'})

        previous_objects = set(frame["objects"])
        previous_texts = set(frame["texts"])

    return facts


def passages(lines: list[dict]) -> list[list[dict]]:
    """Group consecutive transcript lines into passages.

    A new passage starts when adding the next line would take it past PASSAGE_WORDS words, or after a long pause.
    A single line longer than that stays whole, as a passage of its own.
    """
    groups: list[list[dict]] = []
    words = 0

    for line in lines:
        line_words = len(line["text"].split())
        long_pause = bool(groups) and line["start"] - groups[-1][-1]["end"] > PASSAGE_GAP_SECONDS

        if not groups or words + line_words > PASSAGE_WORDS or long_pause:
            groups.append([])
            words = 0

        groups[-1].append(line)
        words += line_words

    return groups


def setting_at(frames: list[dict], offset_seconds: float) -> str | None:
    """Return the setting of the frame nearest in time, so speech can say what kind of space it was heard in."""
    if not frames:
        return None

    nearest = min(frames, key=lambda frame: abs(frame["offset_seconds"] - offset_seconds))

    return nearest["setting"]


def speech_facts(lines: list[dict], frames: list[dict], place: str | None) -> list[dict]:
    """Return one fact per passage of speech, with who said what."""
    facts = []

    for group in passages(lines):
        first = group[0]
        where_when = context(first["at"], place, setting_at(frames, first["start"]))
        spoken = " ".join(f"{line['speaker'] or 'Someone'}: {line['text']}" for line in group)

        facts.append({
            "frame_id": None,
            "at": first["at"],
            "offset_seconds": first["start"],
            "kind": "speech",
            "text": f"{where_when}. {spoken}",
        })

    return facts


def build_facts(conn, session_id: str) -> dict[str, int]:
    """Rebuild and embed every fact for a session. Return how many of each kind were made."""
    _, lat, lon = db.session_info(conn, session_id)
    place = place_for(lat, lon, db.places(conn))

    frames = db.captioned_frames(conn, session_id)
    lines = db.transcript_lines(conn, session_id)

    facts = frame_facts(frames, place) + speech_facts(lines, frames, place)
    vectors = llm.embed(conn, [fact["text"] for fact in facts], "embed") if facts else []

    with conn.transaction():
        db.replace_facts(conn, session_id, facts, vectors)

    counts: dict[str, int] = {}
    for fact in facts:
        counts[fact["kind"]] = counts.get(fact["kind"], 0) + 1

    return counts


def main() -> int:
    parser = argparse.ArgumentParser(description="Build and embed a session's facts.")
    parser.add_argument("session_id")
    args = parser.parse_args()

    with db.connect() as conn:
        counts = build_facts(conn, args.session_id)
        usd = db.step_cost(conn, "embed")

    if not counts:
        print("No facts. Run the captions and transcript steps first.")
        return 0

    for kind in ("moment", "object", "text", "speech"):
        print(f"  {counts.get(kind, 0):>4}  {kind}")

    print(f"Embedded {sum(counts.values())} facts. Total spent on embedding so far: ${usd:.5f}.")

    return 0


if __name__ == "__main__":
    sys.exit(main())
