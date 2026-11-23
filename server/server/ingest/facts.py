"""Turn a moment's captions and transcript into facts that each cover a stretch of time, each embedded."""

import argparse
import sys
from datetime import timedelta

from server import db
from server.shared import llm
from server.shared.config import SUMMARY_MODEL, minutes_seconds, spoken_length
from server.ingest.activities import write_activities
from server.ingest.changes import write_changes
from server.ingest.spots import place_things
from server.ingest.link import link_moment
from server.ingest.overview import write_overview
from server.shared.gps import place_for
from server.ingest.scenes import (
    Said,
    best_crop,
    group_frames,
    group_passages,
    listed_objects,
    places_seen,
    relevant,
    remember,
    spoken,
    stretches,
    summarise_stretch,
    things_by_name,
    vector,
)

KINDS = (
    "frame", "person", "object", "text", "speech", "said", "summary", "recording", "activity", "conversation", "change",
)


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


def stretch_facts(
    frames: list[dict], passages: list[list[dict]], place: str | None, stretch, known: list[dict], scene: int
) -> list[dict]:
    """Return one scene's summary, things, people, text and said facts, adding what it saw to `known`."""
    where = at_place(place, frames[0].get("room") if frames else None)
    context = first_sentence(stretch.happened) if stretch else first_sentence(frames[0]["summary"]) if frames else ""
    facts = []

    if stretch:
        facts.append(summary_fact(frames, passages, f"{stretch.happened}{where}", stretch.title))
        facts += said_facts(passages, stretch.said, place)

    for thing in stretch.things if stretch else things_by_name(frames):
        shown = [frames[number - 1] for number in sorted(set(thing.photos)) if 0 < number <= len(frames)]

        if shown:
            item = remember(known, thing, shown, scene, listed_objects(frames, thing.objects))
            details = f" {thing.details.rstrip('.')}." if thing.details else ""
            screen = " Seen on a screen." if thing.on_screen else ""
            seen_at = places_seen(frames, thing.objects, thing.where)
            fact = covering("object", f"{thing.name}: {seen_at}.{details}{screen}{where} {context}", shown)
            facts.append({**fact, "thing": item["label"]})

    people = grouped_by(
        frames, lambda frame: sorted({(p["id"], p["name"]) for p in frame.get("people", []) if p["name"]})
    )
    facts += [
        {
            **covering("person", f"{name} was there.{where} {context}", shown),
            "person_id": person_id,
            "person_name": name,
        }
        for (person_id, name), shown in people.items()
    ]

    texts = grouped_by(frames, lambda frame: list(dict.fromkeys(frame["texts"])))
    facts += [
        covering("text", f'Text on {surface}: "{text}".{where} {context}', shown)
        for (text, surface), shown in texts.items()
    ]

    return [{**fact, "scene": scene} for fact in facts]


def all_stretch_facts(conn, groups: list[tuple[list, list]], place: str | None, known: list[dict]) -> list[dict]:
    """Ask about each scene in order, so each knows what earlier scenes in its rooms saw, and return all their facts."""
    facts = []

    for scene, (frames, passages) in enumerate(groups):
        stretch, usage = summarise_stretch(frames, passages, relevant(known, frames, scene))
        llm.log_cost(conn, "summary", SUMMARY_MODEL, usage)
        facts += stretch_facts(frames, passages, place, stretch, known, scene)

    return facts


def save_world(conn, moment_id: str, facts: list[dict], known: list[dict], thing_vectors: list[str]) -> None:
    """Store the moment's scenes, things and sightings, and point each fact at its scene and thing or person."""
    db.forget_world(conn, moment_id)

    scenes = {fact["scene"]: db.save_scene(conn, moment_id, fact) for fact in facts if fact["kind"] == "summary"}
    things = {item["label"]: db.new_thing(conn, item["name"], item["details"], *best_crop(item)) for item in known}
    db.save_thing_vectors(conn, {things[item["label"]]: vector for item, vector in zip(known, thing_vectors)})
    people = {
        fact["person_id"]: db.person_entity(conn, fact["person_id"], fact["person_name"])
        for fact in facts
        if fact.get("person_id")
    }

    rows = [
        (
            things[item["label"]],
            frame["id"],
            scenes.get(scene),
            frame["at"],
            place,
            details,
            box,
            source,
        )
        for item in known
        for frame, place, details, source, scene, box in item["sightings"]
    ]
    rows += [
        (people[fact["person_id"]], frame_id, scenes.get(fact["scene"]), fact["at"], None, None, None, "seen")
        for fact in facts
        if fact.get("person_id")
        for frame_id in fact["frame_ids"]
    ]
    db.save_sightings(conn, rows)

    for fact in facts:
        fact["episode_id"] = scenes.get(fact.get("scene"))
        fact["entity_id"] = things.get(fact.get("thing")) or people.get(fact.get("person_id"))


def add_recording_fact(conn, moment_id: str, place: str | None) -> None:
    """Store one fact for the whole recording: its title, overview, when it ran and for how long."""
    title, overview = db.moment_overview(conn, moment_id)
    started_at = db.moment_start_and_location(conn, moment_id)[0]
    seconds = db.moment_length(conn, moment_id)
    start = started_at.astimezone()
    end = start + timedelta(seconds=seconds)
    text = (
        f'Recording "{title or "untitled"}": {overview or "no overview"} It ran from {start:%a %d %b %H:%M:%S}'
        f" to {end:%H:%M:%S}, {spoken_length(seconds)} long.{at_place(place)}"
    )
    fact = {"kind": "recording", "at": started_at, "end_offset_seconds": seconds, "text": text, "title": title}

    db.add_fact(conn, moment_id, fact, llm.embed(conn, [text], "embed")[0])
    conn.commit()


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


def refresh_speech_facts(conn, moment_id: str) -> int:
    """Write a moment's speech facts again from its transcript, so they show who spoke. Return how many there are."""
    _, lat, lon = db.moment_start_and_location(conn, moment_id)
    place = place_for(lat, lon, db.gps_places(conn))
    passages = group_passages(db.transcript_lines(conn, moment_id))
    facts = [speech_fact(passage, place, "speech", spoken(passage)) for passage in passages]
    vectors = llm.embed(conn, [fact["text"] for fact in facts], "embed") if facts else []
    db.add_facts(conn, moment_id, "speech", facts, vectors)

    return len(facts)


def build_facts(conn, moment_id: str) -> dict[str, int]:
    """Rebuild and embed every fact for a moment. Return how many of each kind were made."""
    llm.require_price(SUMMARY_MODEL)

    place, frames, frame_list, frame_vectors, passages = moment_stretches(conn, moment_id)
    groups = stretches(frames, [vector(text) for text in frame_vectors], passages)

    scene_of_frame = {frame["id"]: scene for scene, (members, _) in enumerate(groups) for frame in members}
    scene_of_passage = {id(passage): scene for scene, (_, heard) in enumerate(groups) for passage in heard}

    for fact in frame_list:
        fact["scene"] = scene_of_frame.get(fact["frame_id"])

    known: list[dict] = []
    rest = [
        {**speech_fact(passage, place, "speech", spoken(passage)), "scene": scene_of_passage.get(id(passage))}
        for passage in passages
    ]
    rest += all_stretch_facts(conn, groups, place, known)
    rest_vectors = llm.embed(conn, [fact["text"] for fact in rest], "embed") if rest else []
    thing_texts = [f"{item['name']}: {item['details'] or ''}" for item in known]
    thing_vectors = llm.embed(conn, thing_texts, "embed") if thing_texts else []

    facts = frame_list + rest

    with conn.transaction():
        db.lock_moment(conn, moment_id)
        save_world(conn, moment_id, facts, known, thing_vectors)
        db.replace_facts(conn, moment_id, facts, frame_vectors + rest_vectors)

    place_things(conn, moment_id)
    conn.commit()
    link_moment(conn, moment_id)
    changes = write_changes(conn, moment_id)

    write_overview(conn, moment_id)
    add_recording_fact(conn, moment_id, place)
    grouped = write_activities(conn, moment_id, passages)

    return count_kinds(facts) | {"recording": 1, "change": changes} | grouped


def print_stretches(conn, moment_id: str) -> None:
    """Print the stretches a moment would get, how long each is and why each ended, without calling a model."""
    _, frames, _, frame_vectors, passages = moment_stretches(conn, moment_id)
    groups = group_frames(frames, [vector(text) for text in frame_vectors])

    print(f"{moment_id}: {len(frames)} frames, {len(passages)} passages, {len(groups)} stretches")

    for members, reason in groups:
        start, end = frames[members[0]]["offset_seconds"], frames[members[-1]]["offset_seconds"]
        rooms = {frames[index].get("room") for index in members} - {None}

        print(
            f"  {minutes_seconds(start):>6} to {minutes_seconds(end):>6}  {len(members):>3} frames  {reason:<16} {', '.join(rooms)}"
        )


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
