"""Turn a moment's captions and transcript into facts that each cover a stretch of time, each embedded."""

import argparse
import json
import sys
from bisect import bisect_right
from concurrent.futures import ThreadPoolExecutor

import numpy as np
from pydantic import BaseModel, Field

from memory import db, llm
from memory.config import SUMMARY_MODEL, minutes_seconds
from memory.overview import write_overview
from memory.gps import place_for

PASSAGE_WORDS = 60
PASSAGE_GAP_SECONDS = 8.0
PARALLEL_CALLS = 8

STRETCH_MAX_SECONDS = 120.0
STRETCH_GAP_SECONDS = 30.0
STRETCH_SIMILARITY = 0.6

KINDS = ("frame", "person", "object", "text", "speech", "said", "summary")

SUMMARY_INSTRUCTIONS = """\
You are given one stretch of what someone's camera glasses recorded: what the wearer saw, as numbered photos with
a description and the things in each, and what was said, as numbered passages of transcript. Speakers are labelled
with letters.

Write, using only what is given:
- title: two to five words on what this stretch was about, so someone scanning a list can tell stretches apart.
- happened: two or three sentences on what took place, combining what was seen and said.
- things: everything seen, each thing once. The same thing is often described in different words in different
  photos; merge those into one. Give each a short name, where it was in the scene, and the numbers of the photos it
  is in. Say where it was in plain words, never by photo number.
- said: for each passage, its number and one or two plain sentences stating what was said and by which speaker.
  Leave out filler words. Keep names, numbers and other specifics."""


class Said(BaseModel):
    passage: int = Field(description="The number of the passage this is about")
    text: str = Field(description="What was said in that passage, in one or two plain sentences")


class Thing(BaseModel):
    name: str = Field(description="A short name for the thing")
    where: str = Field(description="Where it was in the scene, without photo numbers")
    photos: list[int] = Field(description="The numbers of the photos it is in")


class Stretch(BaseModel):
    title: str = Field(description="Two to five words on what this stretch was about")
    happened: str = Field(description="What took place, in two or three sentences")
    things: list[Thing] = Field(description="Each thing seen, once")
    said: list[Said] = Field(description="One entry per passage; empty if nothing was said")


def at_place(place: str | None, room: str | None = None) -> str:
    """Return where it was, e.g. ' Room: Kitchen. At Home.', or nothing."""
    return (f" Room: {room}." if room else "") + (f" At {place}." if place else "")


def first_sentence(text: str) -> str:
    """Return the text up to its first full stop."""
    head, stop, _ = text.partition(". ")

    return head + "." if stop else text


def covering(kind: str, text: str, frames: list[dict]) -> dict:
    """Return a fact that covers these frames, shown with the first of them."""
    return {
        "frame_id": frames[0]["id"],
        "frame_ids": [frame["id"] for frame in frames],
        "at": frames[0]["at"],
        "offset_seconds": frames[0]["offset_seconds"],
        "end_offset_seconds": frames[-1]["offset_seconds"],
        "kind": kind,
        "text": text,
    }


def frame_fact(frame: dict, place: str | None) -> dict:
    """Return the fact that describes one photo as a whole, with who was in it."""
    names = sorted({person["name"] for person in frame.get("people", []) if person["name"]})
    with_people = f" With {', '.join(names)}." if names else ""

    return covering("frame", f"{frame['summary']}{with_people}{at_place(place, frame.get('room'))}", [frame])


def vector(text: str) -> np.ndarray:
    """Return a stored embedding as numbers."""
    return np.array(json.loads(text))


def cosine(a: np.ndarray, b: np.ndarray) -> float:
    return float(a @ b / (np.linalg.norm(a) * np.linalg.norm(b)))


def stretch_break(frames: list[dict], vectors: list, group: list[int], index: int) -> str | None:
    """Return why a frame starts a new stretch instead of joining `group`, or None if it joins."""
    frame, first, last = frames[index], frames[group[0]], frames[group[-1]]

    if frame.get("room") and first.get("room") and frame["room"] != first["room"]:
        return "room changed"

    if frame["offset_seconds"] - last["offset_seconds"] > STRETCH_GAP_SECONDS:
        return "gap"

    if frame["offset_seconds"] - first["offset_seconds"] >= STRETCH_MAX_SECONDS:
        return "length"

    centre = np.mean([vectors[member] for member in group], axis=0)

    if cosine(vectors[index], centre) < STRETCH_SIMILARITY:
        return "looks different"

    return None


def group_frames(frames: list[dict], vectors: list) -> list[tuple[list[int], str]]:
    """Split frames in order into stretches; return each stretch's frame numbers and why it ended."""
    groups: list[tuple[list[int], str]] = []

    for index in range(len(frames)):
        reason = stretch_break(frames, vectors, groups[-1][0], index) if groups else None

        if groups and reason is None:
            groups[-1][0].append(index)
            continue

        if groups:
            groups[-1] = (groups[-1][0], reason)

        groups.append(([index], "end"))

    return groups


def speech_groups(passages: list[list[dict]]) -> list[list[list[dict]]]:
    """Split passages into stretches of at most STRETCH_MAX_SECONDS, for a moment with no photos."""
    groups: list[list[list[dict]]] = []

    for passage in passages:
        if not groups or passage[0]["start"] - groups[-1][0][0]["start"] >= STRETCH_MAX_SECONDS:
            groups.append([])

        groups[-1].append(passage)

    return groups


def stretches(frames: list[dict], vectors: list, passages: list[list[dict]]) -> list[tuple[list, list]]:
    """Return the moment's stretches as (frames, passages); each passage joins the stretch it starts in."""
    if not frames:
        return [([], group) for group in speech_groups(passages)]

    groups = [[frames[index] for index in members] for members, _ in group_frames(frames, vectors)]
    starts = [group[0]["offset_seconds"] for group in groups]
    heard: list[list] = [[] for _ in groups]

    for passage in passages:
        heard[max(bisect_right(starts, passage[0]["start"]) - 1, 0)].append(passage)

    return list(zip(groups, heard))


def group_passages(lines: list[dict]) -> list[list[dict]]:
    """Group transcript lines into passages, split at PASSAGE_WORDS words or a long pause."""
    passages: list[list[dict]] = []
    words = 0

    for line in lines:
        line_words = len(line["text"].split())
        long_pause = bool(passages) and line["start"] - passages[-1][-1]["end"] > PASSAGE_GAP_SECONDS

        if not passages or words + line_words > PASSAGE_WORDS or long_pause:
            passages.append([])
            words = 0

        passages[-1].append(line)
        words += line_words

    return passages


def spoken(passage: list[dict]) -> str:
    """Return a passage as one string with each line's speaker."""
    return " ".join(f"{line['speaker'] or 'Someone'}: {line['text']}" for line in passage)


def speech_fact(passage: list[dict], place: str | None, kind: str, text: str) -> dict:
    """Return a fact that covers one passage."""
    return {
        "frame_id": None,
        "frame_ids": [],
        "at": passage[0]["at"],
        "offset_seconds": passage[0]["start"],
        "end_offset_seconds": passage[-1]["end"],
        "kind": kind,
        "text": f"{text}{at_place(place)}",
    }


def describe_photo(number: int, frame: dict) -> str:
    """Return one photo as the line the stretch call reads."""
    things = "; ".join(f"{name} ({location})" for name, location in frame["objects"])

    return f"{number}. {int(frame['offset_seconds'])} s: {frame['summary']} Things: {things or 'none listed'}"


def summarise_stretch(frames: list[dict], passages: list[list[dict]]):
    """Ask the model for one stretch's title, what happened, the things seen and what each passage said."""
    seen = "\n".join(describe_photo(number, frame) for number, frame in enumerate(frames, start=1))
    heard = "\n".join(f"{number}. {spoken(passage)}" for number, passage in enumerate(passages, start=1))

    response = llm.client().responses.parse(
        model=SUMMARY_MODEL,
        instructions=SUMMARY_INSTRUCTIONS,
        input=f"Seen:\n{seen or 'Nothing.'}\n\nSaid:\n{heard or 'Nothing.'}",
        text_format=Stretch,
        reasoning={"effort": "low"},
    )

    return response.output_parsed, response.usage


def things_by_name(frames: list[dict]) -> list[Thing]:
    """Return each object named in the frames once, for when the stretch call gave nothing back."""
    found: dict[str, Thing] = {}

    for number, frame in enumerate(frames, start=1):
        for name, location in frame["objects"]:
            found.setdefault(name.lower(), Thing(name=name, where=location, photos=[])).photos.append(number)

    return list(found.values())


def grouped_by(frames: list[dict], keys) -> dict:
    """Return, for each key `keys(frame)` gives, the frames it appears in, in order."""
    found: dict = {}

    for frame in frames:
        for key in keys(frame):
            found.setdefault(key, []).append(frame)

    return found


def said_facts(passages: list[list[dict]], said: list[Said], place: str | None) -> list[dict]:
    """Return a 'said' fact for each passage the model summarised, matched by passage number."""
    by_number = {item.passage: item.text for item in reversed(said)}

    return [
        speech_fact(passage, place, "said", by_number[number])
        for number, passage in enumerate(passages, start=1)
        if number in by_number
    ]


def summary_fact(frames: list[dict], passages: list[list[dict]], text: str, title: str) -> dict:
    """Return the fact that sums up a whole stretch."""
    starts = [(frame["offset_seconds"], frame["at"]) for frame in frames]
    starts += [(passage[0]["start"], passage[0]["at"]) for passage in passages]
    offset, at = min(starts)
    end = max([frame["offset_seconds"] for frame in frames] + [passage[-1]["end"] for passage in passages])

    return {
        "frame_id": None,
        "frame_ids": [frame["id"] for frame in frames],
        "at": at,
        "offset_seconds": offset,
        "end_offset_seconds": end,
        "kind": "summary",
        "text": text,
        "title": title,
    }


def stretch_facts(frames: list[dict], passages: list[list[dict]], place: str | None, stretch) -> list[dict]:
    """Return one stretch's summary, things, people, text and said facts."""
    where = at_place(place, frames[0].get("room") if frames else None)
    context = first_sentence(stretch.happened) if stretch else first_sentence(frames[0]["summary"]) if frames else ""
    facts = []

    if stretch:
        facts.append(summary_fact(frames, passages, f"{stretch.happened}{where}", stretch.title))
        facts += said_facts(passages, stretch.said, place)

    for thing in stretch.things if stretch else things_by_name(frames):
        shown = [frames[number - 1] for number in sorted(set(thing.photos)) if 0 < number <= len(frames)]

        if shown:
            facts.append(covering("object", f"{thing.name}: {thing.where}.{where} {context}", shown))

    people = grouped_by(frames, lambda frame: sorted({p["name"] for p in frame.get("people", []) if p["name"]}))
    facts += [covering("person", f"{name} was there.{where} {context}", shown) for name, shown in people.items()]

    texts = grouped_by(frames, lambda frame: list(dict.fromkeys(frame["texts"])))
    facts += [
        covering("text", f'Text on {surface}: "{text}".{where} {context}', shown)
        for (text, surface), shown in texts.items()
    ]

    return facts


def all_stretch_facts(conn, groups: list[tuple[list, list]], place: str | None) -> list[dict]:
    """Ask about every stretch at once and return all their facts."""
    with ThreadPoolExecutor(PARALLEL_CALLS) as pool:
        results = list(pool.map(lambda group: summarise_stretch(*group), groups))

    facts = []

    for (frames, passages), (stretch, usage) in zip(groups, results):
        llm.log_cost(conn, "summary", SUMMARY_MODEL, usage)
        facts += stretch_facts(frames, passages, place, stretch)

    return facts


def count_kinds(facts: list[dict]) -> dict[str, int]:
    """Return how many facts there are of each kind."""
    counts: dict[str, int] = {}

    for fact in facts:
        counts[fact["kind"]] = counts.get(fact["kind"], 0) + 1

    return counts


def moment_stretches(conn, moment_id: str) -> tuple:
    """Return a moment's place, frames, frame facts with their vectors, passages and stretches."""
    _, lat, lon = db.moment_start_and_location(conn, moment_id)
    place = place_for(lat, lon, db.gps_places(conn))

    frames = db.captioned_frames(conn, moment_id)
    passages = group_passages(db.transcript_lines(conn, moment_id))

    frame_list = [frame_fact(frame, place) for frame in frames]
    frame_vectors = llm.embed(conn, [fact["text"] for fact in frame_list], "embed") if frame_list else []

    return place, frames, frame_list, frame_vectors, passages


def build_facts(conn, moment_id: str) -> dict[str, int]:
    """Rebuild and embed every fact for a moment. Return how many of each kind were made."""
    llm.require_price(SUMMARY_MODEL)

    place, frames, frame_list, frame_vectors, passages = moment_stretches(conn, moment_id)
    groups = stretches(frames, [vector(text) for text in frame_vectors], passages)

    rest = [speech_fact(passage, place, "speech", spoken(passage)) for passage in passages]
    rest += all_stretch_facts(conn, groups, place)
    rest_vectors = llm.embed(conn, [fact["text"] for fact in rest], "embed") if rest else []

    facts = frame_list + rest

    with conn.transaction():
        db.replace_facts(conn, moment_id, facts, frame_vectors + rest_vectors)

    write_overview(conn, moment_id)

    return count_kinds(facts)


def print_stretches(conn, moment_id: str) -> None:
    """Print the stretches a moment would get, how long each is and why each ended, without calling a model."""
    _, frames, _, frame_vectors, passages = moment_stretches(conn, moment_id)
    groups = group_frames(frames, [vector(text) for text in frame_vectors])

    print(f"{moment_id}: {len(frames)} frames, {len(passages)} passages, {len(groups)} stretches")

    for members, reason in groups:
        start, end = frames[members[0]]["offset_seconds"], frames[members[-1]]["offset_seconds"]
        rooms = {frames[index].get("room") for index in members} - {None}

        print(f"  {minutes_seconds(start):>6} to {minutes_seconds(end):>6}  {len(members):>3} frames  {reason:<16} {', '.join(rooms)}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Build and embed a moment's facts.")
    parser.add_argument("moment_ids", nargs="+")
    parser.add_argument("--stretches", action="store_true", help="only print the stretches each moment would get")
    args = parser.parse_args()

    with db.connect() as conn:
        for moment_id in args.moment_ids:
            if args.stretches:
                print_stretches(conn, moment_id)
                continue

            counts = build_facts(conn, moment_id)
            print(moment_id, "  ".join(f"{counts.get(kind, 0)} {kind}" for kind in KINDS))

        usd = db.step_cost(conn, "embed") + db.step_cost(conn, "summary")

    print(f"Total spent on summaries and embedding so far: ${usd:.5f}.")

    return 0


if __name__ == "__main__":
    sys.exit(main())
