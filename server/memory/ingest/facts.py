"""Turn a session's captions and transcript into small facts and summaries, each embedded."""

import argparse
import sys
from concurrent.futures import ThreadPoolExecutor

from pydantic import BaseModel, Field

from memory import db, llm
from memory.config import STRETCH_SECONDS, SUMMARY_MODEL
from memory.places import place_for

PASSAGE_WORDS = 60
PASSAGE_GAP_SECONDS = 8.0
PARALLEL_CALLS = 8

KINDS = ("moment", "object", "text", "speech", "said", "summary")

SUMMARY_INSTRUCTIONS = """\
You are given about a minute of what someone's camera glasses recorded: what the wearer saw, as timed
descriptions of frames, and what was said, as numbered passages of transcript. Speakers are labelled with
letters.

Write two things, using only what is given:
- happened: two or three sentences on what took place in this minute, combining what was seen and said.
- said: for each passage, its number and one or two plain sentences stating what was said and by which
  speaker. Leave out filler words. Keep names, numbers and other specifics."""


class Said(BaseModel):
    passage: int = Field(description="The number of the passage this is about")
    text: str = Field(description="What was said in that passage, in one or two plain sentences")


class Stretch(BaseModel):
    happened: str = Field(description="What took place in this minute, in two or three sentences")
    said: list[Said] = Field(description="One entry per passage; empty if nothing was said")


def at_place(place: str | None) -> str:
    """Return ' At Home.' for a named place, or nothing."""
    return f" At {place}." if place else ""


def first_sentence(text: str) -> str:
    """Return the text up to its first full stop."""
    head, stop, _ = text.partition(". ")

    return head + "." if stop else text


def frame_facts(frames: list[dict], place: str | None) -> list[dict]:
    """Return a moment fact for each frame, and a fact for each new object and piece of text in it."""
    facts = []
    previous_objects: set = set()
    previous_texts: set = set()

    for frame in frames:
        where = at_place(place)
        scene = first_sentence(frame["summary"])
        base = {"frame_id": frame["id"], "at": frame["at"], "offset_seconds": frame["offset_seconds"]}

        facts.append({**base, "kind": "moment", "text": f"{frame['summary']}{where}"})

        for name, location in frame["objects"]:
            if (name, location) not in previous_objects:
                facts.append({**base, "kind": "object", "text": f"{name}: {location}.{where} {scene}"})

        for text, surface in frame["texts"]:
            if (text, surface) not in previous_texts:
                facts.append({**base, "kind": "text", "text": f'Text on {surface}: "{text}".{where} {scene}'})

        previous_objects = set(frame["objects"])
        previous_texts = set(frame["texts"])

    return facts


def passages(lines: list[dict]) -> list[list[dict]]:
    """Group transcript lines into passages, split at PASSAGE_WORDS words or a long pause."""
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


def spoken(passage: list[dict]) -> str:
    """Return a passage as one string with each line's speaker."""
    return " ".join(f"{line['speaker'] or 'Someone'}: {line['text']}" for line in passage)


def speech_fact(passage: list[dict], place: str | None, kind: str, text: str) -> dict:
    """Return a fact placed at the start of a passage."""
    first = passage[0]

    return {
        "frame_id": None,
        "at": first["at"],
        "offset_seconds": first["start"],
        "kind": kind,
        "text": f"{text}{at_place(place)}",
    }


def stretches(frames: list[dict], groups: list[list[dict]]) -> list[tuple[list, list]]:
    """Split frames and passages into consecutive stretches of STRETCH_SECONDS."""
    slots: dict[int, tuple[list, list]] = {}

    for frame in frames:
        slots.setdefault(int(frame["offset_seconds"] // STRETCH_SECONDS), ([], []))[0].append(frame)

    for passage in groups:
        slots.setdefault(int(passage[0]["start"] // STRETCH_SECONDS), ([], []))[1].append(passage)

    return [slots[slot] for slot in sorted(slots)]


def summarise(frames: list[dict], groups: list[list[dict]]):
    """Ask the model what happened in one stretch and what each passage said. Return (Stretch, usage)."""
    seen = "\n".join(f"{int(frame['offset_seconds'])} s: {frame['summary']}" for frame in frames)
    heard = "\n".join(f"{number}. {spoken(passage)}" for number, passage in enumerate(groups, start=1))

    response = llm.client().responses.parse(
        model=SUMMARY_MODEL,
        instructions=SUMMARY_INSTRUCTIONS,
        input=f"Seen:\n{seen or 'Nothing.'}\n\nSaid:\n{heard or 'Nothing.'}",
        text_format=Stretch,
        reasoning={"effort": "low"},
    )

    return response.output_parsed, response.usage


def summary_fact(frames: list[dict], groups: list[list[dict]], place: str | None, happened: str) -> dict:
    """Return a summary fact placed at the earliest frame or passage of a stretch."""
    starts = [(frame["offset_seconds"], frame["at"]) for frame in frames]
    starts += [(passage[0]["start"], passage[0]["at"]) for passage in groups]
    offset, at = min(starts)

    return {
        "frame_id": None,
        "at": at,
        "offset_seconds": offset,
        "kind": "summary",
        "text": f"{happened}{at_place(place)}",
    }


def said_facts(groups: list[list[dict]], said: list[Said], place: str | None) -> list[dict]:
    """Return a 'said' fact for each passage the model summarised, matched by passage number."""
    by_number = {item.passage: item.text for item in reversed(said)}

    return [
        speech_fact(passage, place, "said", by_number[number])
        for number, passage in enumerate(groups, start=1)
        if number in by_number
    ]


def summary_facts(conn, frames: list[dict], groups: list[list[dict]], place: str | None) -> list[dict]:
    """Return a summary fact for each stretch and a 'said' fact for each passage in it."""
    parts = stretches(frames, groups)
    facts = []

    with ThreadPoolExecutor(PARALLEL_CALLS) as pool:
        results = list(pool.map(lambda part: summarise(*part), parts))

    for (stretch_frames, stretch_groups), (stretch, usage) in zip(parts, results):
        llm.log_cost(conn, "summary", SUMMARY_MODEL, usage)

        if stretch:
            facts.append(summary_fact(stretch_frames, stretch_groups, place, stretch.happened))
            facts += said_facts(stretch_groups, stretch.said, place)

    return facts


def count_kinds(facts: list[dict]) -> dict[str, int]:
    """Return how many facts there are of each kind."""
    counts: dict[str, int] = {}

    for fact in facts:
        counts[fact["kind"]] = counts.get(fact["kind"], 0) + 1

    return counts


def build_facts(conn, session_id: str) -> dict[str, int]:
    """Rebuild and embed every fact for a session. Return how many of each kind were made."""
    llm.require_price(SUMMARY_MODEL)

    _, lat, lon = db.session_info(conn, session_id)
    place = place_for(lat, lon, db.places(conn))

    frames = db.captioned_frames(conn, session_id)
    groups = passages(db.transcript_lines(conn, session_id))

    facts = frame_facts(frames, place)
    facts += [speech_fact(passage, place, "speech", spoken(passage)) for passage in groups]
    facts += summary_facts(conn, frames, groups, place)

    vectors = llm.embed(conn, [fact["text"] for fact in facts], "embed") if facts else []

    with conn.transaction():
        db.replace_facts(conn, session_id, facts, vectors)

    return count_kinds(facts)


def main() -> int:
    parser = argparse.ArgumentParser(description="Build and embed a session's facts.")
    parser.add_argument("session_id")
    args = parser.parse_args()

    with db.connect() as conn:
        counts = build_facts(conn, args.session_id)
        usd = db.step_cost(conn, "embed") + db.step_cost(conn, "summary")

    if not counts:
        print("No facts. Run the captions and transcript steps first.")
        return 0

    for kind in KINDS:
        print(f"  {counts.get(kind, 0):>4}  {kind}")

    print(f"Embedded {sum(counts.values())} facts. Total spent on summaries and embedding so far: ${usd:.5f}.")

    return 0


if __name__ == "__main__":
    sys.exit(main())
