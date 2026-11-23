"""Group a recording's photos and speech into scenes, and ask a model what happened in each scene and which things
it saw, keeping one thing per object across the recording."""

import json
from bisect import bisect_right

import numpy as np
from pydantic import BaseModel, Field

from server.shared import llm
from server.shared.config import DATA_DIR, SUMMARY_MODEL
from server.ingest.captions import image_link

PASSAGE_WORDS = 60

PASSAGE_GAP_SECONDS = 8.0

STRETCH_MAX_SECONDS = 120.0

STRETCH_GAP_SECONDS = 30.0

STRETCH_SIMILARITY = 0.6

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


def best_crop(item: dict) -> tuple[int, list | None]:
    """Return the frame and box that show a thing: its first sighting with a box, else its first sighting."""
    boxed = next(((frame["id"], box) for frame, *_, box in item["sightings"] if box), None)

    return boxed or (item["sightings"][0][0]["id"], None)
