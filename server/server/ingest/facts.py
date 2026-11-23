"""Turn a moment's captions and transcript into facts that each cover a stretch of time, each embedded."""

import argparse
import json
import sys
from datetime import timedelta
from bisect import bisect_right

import numpy as np
from pydantic import BaseModel, Field

from server import db, llm
from server.config import DATA_DIR, SUMMARY_MODEL, minutes_seconds, spoken_length
from server.ingest.captions import image_link
from server.ingest.activities import write_activities
from server.ingest.link import link_moment
from server.overview import write_overview
from server.gps import place_for

PASSAGE_WORDS = 60
PASSAGE_GAP_SECONDS = 8.0
STRETCH_MAX_SECONDS = 120.0
STRETCH_GAP_SECONDS = 30.0
STRETCH_SIMILARITY = 0.6

KINDS = ("frame", "person", "object", "text", "speech", "said", "summary", "recording", "activity", "conversation")

SCENE_PHOTOS = 6

SUMMARY_INSTRUCTIONS = """\
You are given one scene of what someone's camera glasses recorded: numbered photos with a description, the people
recognised and the things listed in each, some of the photos themselves, what was said as numbered passages of
transcript (speakers are labelled with letters), and the things already seen earlier in this recording, each with a
label. "Me" is the wearer.

Write, using only what is given:
- title: two to five words on what this scene was about, so someone scanning a list can tell scenes apart.
- happened: two or three sentences on what took place, combining what was seen and said.
- things: everything seen, each thing once. The same thing is often described in different words in different
  photos ("edamame pod", "green bean pods", "bowl of edamame"); that is one thing, not several. Check the
  descriptions against the photos you can see. Give each the most specific name the photos and the speech give,
  where it was in the scene, the details that tell it apart (keep the colours, materials and writing), and the
  numbers of the photos it is in, and which numbered objects in those photos' lists it is ("3.2" is photo 3, object
  [2]). Say where it was in plain words, never by photo number. People recognised by name
  are recorded already: do not list them as things, but do list what they wear or hold, saying whose (e.g. "worn by
  Me"). Before listing a thing as new, check the things seen earlier in this recording; when it is one of them, give
  its label in same_as. Mark things only seen on a screen or in a picture.
- said: for each passage, its number and one or two plain sentences stating what was said and by which speaker.
  Leave out filler words. Keep names, numbers and other specifics."""


class Said(BaseModel):
    passage: int = Field(description="The number of the passage this is about")
    text: str = Field(description="What was said in that passage, in one or two plain sentences")


class Thing(BaseModel):
    name: str = Field(description="The most specific name for the thing")
    where: str = Field(description="Where it was in the scene, without photo numbers")
    details: str = Field(
        description="What tells it apart: colour, material, pattern, writing; keep the photos' details"
    )
    photos: list[int] = Field(description="The numbers of the photos it is in")
    objects: list[str] = Field(
        description="Which listed objects it is, as photo and object number, e.g. '3.2' for photo 3, object [2]"
    )
    same_as: str | None = Field(description="The label of the same thing seen earlier in this recording, or null")
    on_screen: bool = Field(description="True if it was only seen on a screen or in a picture")


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

        if reason == "looks different" and index + 1 < len(frames):
            reason = reason if stretch_break(frames, vectors, groups[-1][0], index + 1) else None

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
    """Return one photo as the line the scene call reads."""
    things = "; ".join(
        f"[{index}] {item['name']} ({item['location']}{'; ' + item['details'] if item.get('details') else ''})"
        for index, item in enumerate(frame["objects"], start=1)
    )
    names = sorted({person["name"] for person in frame.get("people", []) if person["name"]})
    people = f" People: {', '.join(names)}." if names else ""

    return f"{number}. {int(frame['offset_seconds'])} s: {frame['summary']}{people} Things: {things or 'none listed'}"


def chosen_photos(frames: list[dict], count: int = SCENE_PHOTOS) -> list[int]:
    """Return the numbers of up to `count` photos spread evenly through a scene."""
    if len(frames) <= count:
        return list(range(1, len(frames) + 1))

    return sorted({round(1 + index * (len(frames) - 1) / (count - 1)) for index in range(count)})


def relevant(known: list[dict], frames: list[dict], scene: int) -> list[dict]:
    """Return the earlier things a scene could see again: those seen in its rooms, and those in the scene before it."""
    rooms = {frame.get("room") for frame in frames}

    return [
        item
        for item in known
        if any(frame.get("room") in rooms or seen_in == scene - 1 for frame, *_, seen_in, _ in item["sightings"])
    ]


def known_lines(known: list[dict]) -> str:
    """Return the things seen earlier in this recording as labelled lines."""
    return "\n".join(f"{item['label']}: {item['name']} ({item['details'] or 'no details'})" for item in known)


def summarise_stretch(frames: list[dict], passages: list[list[dict]], known: list[dict] = ()):
    """Ask the model for one scene's title, what happened, the things seen and what each passage said."""
    seen = "\n".join(describe_photo(number, frame) for number, frame in enumerate(frames, start=1))
    heard = "\n".join(f"{number}. {spoken(passage)}" for number, passage in enumerate(passages, start=1))
    text = (
        f"Seen:\n{seen or 'Nothing.'}\n\nSaid:\n{heard or 'Nothing.'}"
        f"\n\nSeen earlier in this recording:\n{known_lines(known) or 'Nothing yet.'}"
    )
    content = [{"type": "input_text", "text": text}]

    for number in chosen_photos(frames):
        content.append({"type": "input_text", "text": f"Photo {number}:"})
        content.append(
            {
                "type": "input_image",
                "image_url": image_link(DATA_DIR / frames[number - 1]["image_path"]),
                "detail": "low",
            }
        )

    response = llm.client().responses.parse(
        model=SUMMARY_MODEL,
        instructions=SUMMARY_INSTRUCTIONS,
        input=[{"role": "user", "content": content}],
        text_format=Stretch,
        reasoning={"effort": "medium"},
    )

    return response.output_parsed, response.usage


def things_by_name(frames: list[dict]) -> list[Thing]:
    """Return each object named in the frames once, for when the stretch call gave nothing back."""
    found: dict[str, Thing] = {}

    for number, frame in enumerate(frames, start=1):
        for index, item in enumerate(frame["objects"], start=1):
            thing = Thing(
                name=item["name"],
                where=item["location"],
                details=item.get("details") or "",
                photos=[],
                same_as=None,
                on_screen=False,
                objects=[f"{number}.{index}"],
            )
            found.setdefault(item["name"].lower(), thing).photos.append(number)

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


def matching_box(frame: dict, name: str) -> list | None:
    """Return the box of the object in a frame whose name matches, if any."""
    for item in frame["objects"]:
        if item.get("box") and (item["name"].lower() in name.lower() or name.lower() in item["name"].lower()):
            return [float(value) for value in item["box"]]

    return None


def listed_objects(frames: list[dict], references: list[str]) -> dict[int, dict]:
    """Return the caption object a thing was said to be in each frame, by frame id; references look like '3.2'."""
    found = {}

    for reference in references:
        photo, _, number = reference.partition(".")

        if not (photo.isdigit() and number.isdigit()) or not 0 < int(photo) <= len(frames):
            continue

        frame = frames[int(photo) - 1]

        if 0 < int(number) <= len(frame["objects"]):
            found.setdefault(frame["id"], frame["objects"][int(number) - 1])

    return found


def object_boxes(frames: list[dict], references: list[str]) -> dict[int, list[float]]:
    """Return the box of each listed object a thing was said to be, by frame id."""
    return {
        frame_id: [float(value) for value in item["box"]]
        for frame_id, item in listed_objects(frames, references).items()
        if item.get("box")
    }


def places_seen(frames: list[dict], references: list[str], merged: str) -> str:
    """Return where a thing was, in the captions' own words for each photo, or the merged place if none was listed."""
    places = list(
        dict.fromkeys(item["location"] for item in listed_objects(frames, references).values() if item.get("location"))
    )

    return "; ".join(places) if places else merged


def box_of(item: dict | None) -> list[float] | None:
    """Return a caption object's box as numbers, if it has one."""
    return [float(value) for value in item["box"]] if item and item.get("box") else None


def remember(known: list[dict], thing: Thing, shown: list[dict], scene: int, listed: dict | None = None) -> dict:
    """Add a thing's sightings to the one it is the same as, or to a new one, and return that one."""
    item = next((entry for entry in known if thing.same_as and entry["label"] == thing.same_as), None)

    if item is None:
        item = {"label": f"K{len(known) + 1}", "name": thing.name, "details": thing.details, "sightings": []}
        known.append(item)

    source = "screen" if thing.on_screen else "seen"
    listed = listed or {}
    item["sightings"] += [
        (
            frame,
            listed.get(frame["id"], {}).get("location") or thing.where,
            thing.details,
            source,
            scene,
            box_of(listed.get(frame["id"])) or matching_box(frame, thing.name),
        )
        for frame in shown
    ]

    return item


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


def best_crop(item: dict) -> tuple[int, list | None]:
    """Return the frame and box that show a thing: its first sighting with a box, else its first sighting."""
    boxed = next(((frame["id"], box) for frame, *_, box in item["sightings"] if box), None)

    return boxed or (item["sightings"][0][0]["id"], None)


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

    link_moment(conn, moment_id)

    write_overview(conn, moment_id)
    add_recording_fact(conn, moment_id, place)
    grouped = write_activities(conn, moment_id, passages)

    return count_kinds(facts) | {"recording": 1} | grouped


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
