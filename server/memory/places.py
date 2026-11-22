"""Places (an apartment, a lecture hall) and the rooms inside them, for every frame.

uv run python -m memory.places list
uv run python -m memory.places label <moment id>
uv run python -m memory.places rebuild
"""

import argparse
import math
import shutil
import sys
from collections import Counter
from pathlib import Path

import numpy as np
from pydantic import BaseModel, Field

from memory import db, llm, storage
from memory.config import DATA_DIR, SUMMARY_MODEL
from memory.ingest.facts import build_facts
from memory.ingest.poses import load_poses
from memory.gps import distance_m

MAPS_DIR = DATA_DIR / "places"
MOMENTS_DIR = DATA_DIR / "moments"
PLACE_SEARCH_METRES = 150
SAME_PLACE_SIMILARITY = 0.75
TRAVEL_METRES = 30
NEIGHBOUR_METRES = 1.5

INSTRUCTIONS = """\
You are given everything recorded in one moment by a person's camera: each frame's description, when it was taken,
and where the camera stood (x and z in metres, in this moment's own map), plus what was said and, when there is one,
the size of the place the phone's lidar scanned.

Work out which room the person was standing in for every frame. A frame describes what the camera saw, which can be
another room seen through a doorway or a close-up of a screen, a page or a hand; judge from the neighbouring frames,
the positions, the speech and the scan, not only from that one description. Frames taken close together in place
and time are usually in the same room.

Name each room in a few plain words, the way the person would say it. When a room matches one of the known rooms of
this place, use that name exactly. A place can be a single room. Also name the whole place in a few words."""


class RoomInfo(BaseModel):
    name: str = Field(description="The room's name in a few plain words")
    description: str = Field(description="One sentence on what makes this room recognisable")


class FrameRoom(BaseModel):
    frame: int = Field(description="The frame's number")
    room: str = Field(description="The name of the room the person was in, from rooms")


class Labels(BaseModel):
    place: str = Field(description="The whole place in a few words")
    rooms: list[RoomInfo]
    frames: list[FrameRoom]


def capitalised(name: str) -> str:
    name = name.strip()

    return name[:1].upper() + name[1:]


def unit(vector: np.ndarray) -> np.ndarray:
    return vector / np.linalg.norm(vector)


def parse_vector(text: str) -> np.ndarray:
    return np.array([float(value) for value in text.strip("[]").split(",")])


def travelled(poses: list[list[float]]) -> bool:
    """Return whether the phone went farther than a building's rooms from where it started."""
    if not poses:
        return False

    x0, _, z0 = poses[0][1:4]

    return max(math.hypot(x - x0, z - z0) for _, x, _, z, *_ in poses) > TRAVEL_METRES


def smooth(frame_rooms: list[tuple], mapped_rooms: list[tuple], metres: float = NEIGHBOUR_METRES) -> dict:
    """Give each placed frame the most common room among frames within `metres` of it, keeping ties as they were."""
    placed = [(x, z, room) for _, x, z, room in frame_rooms if x is not None] + list(mapped_rooms)
    smoothed = {}

    for frame_id, x, z, room in frame_rooms:
        if x is None:
            smoothed[frame_id] = room
            continue

        votes = Counter(other for ox, oz, other in placed if math.hypot(ox - x, oz - z) <= metres)
        best, count = votes.most_common(1)[0]
        smoothed[frame_id] = best if count > votes[room] else room

    return smoothed


def scan_note(moment_dir: Path) -> str | None:
    """Describe how far the phone moved and how big the lidar scan is, if the moment has them."""
    notes = []
    poses_file = moment_dir / "poses.json"
    mesh_file = moment_dir / "mesh.obj"

    if poses_file.is_file() and (poses := load_poses(poses_file)):
        x0, _, z0 = poses[0][1:4]
        farthest = max(math.hypot(x - x0, z - z0) for _, x, _, z, *_ in poses)
        notes.append(f"The phone moved at most {farthest:.1f} m from where it started.")

    if mesh_file.is_file():
        with open(mesh_file) as mesh:
            points = np.array([line.split()[1:4] for line in mesh if line.startswith("v ")], dtype=float)

        if len(points):
            low, high = np.percentile(points, 2, axis=0), np.percentile(points, 98, axis=0)
            notes.append(
                f"The lidar scan spans about {high[0] - low[0]:.0f} by {high[2] - low[2]:.0f} m and is "
                f"{high[1] - low[1]:.1f} m tall."
            )

    return " ".join(notes) or None


def moment_vector(conn, moment_id: str) -> np.ndarray | None:
    """Embed a moment's frame descriptions and return their average direction."""
    summaries = [frame["summary"] for frame in db.captioned_frames(conn, moment_id)]

    if not summaries:
        return None

    return unit(np.mean([parse_vector(vector) for vector in llm.embed(conn, summaries, "place")], axis=0))


def places_near(conn, lat, lon) -> list[dict]:
    """Return the places near a location, most recently updated first; all of them when the location is unknown."""
    return [
        place
        for place in db.list_places(conn)
        if lat is None
        or place["lat"] is None
        or distance_m(lat, lon, place["lat"], place["lon"]) <= PLACE_SEARCH_METRES
    ]


def matching_place(conn, moment_id: str, lat, lon) -> int | None:
    """Return the nearby place whose frames look most like this moment's, if they look alike enough."""
    vector = moment_vector(conn, moment_id)

    if vector is None:
        return None

    close = {place["id"] for place in places_near(conn, lat, lon)}
    scores = {
        place_id: float(unit(parse_vector(text)) @ vector)
        for place_id, text in db.place_vectors(conn).items()
        if place_id in close
    }

    best = max(scores, key=scores.get, default=None)

    return best if best is not None and scores[best] >= SAME_PLACE_SIMILARITY else None


def keep_map(place_id: int, world_map: Path) -> str:
    """Copy a world map into the maps folder and return its path under the data folder."""
    MAPS_DIR.mkdir(parents=True, exist_ok=True)
    shutil.copy2(world_map, MAPS_DIR / f"{place_id}.map")
    storage.save(MAPS_DIR / f"{place_id}.map")

    return f"places/{place_id}.map"


def assign_place(conn, moment_id: str, moment_dir: Path, recognised_place_id: int | None) -> int | None:
    """Put a moment at a place: the one the phone recognised, one that looks the same, or a new one."""
    storage.fetch_folder(moment_dir)
    world_map = moment_dir / "world.map"
    poses_file = moment_dir / "poses.json"
    has_map = world_map.is_file() and not (poses_file.is_file() and travelled(load_poses(poses_file)))
    _, lat, lon = db.moment_start_and_location(conn, moment_id)

    if recognised_place_id is not None and has_map:
        db.save_place(conn, recognised_place_id, keep_map(recognised_place_id, world_map), lat, lon)
        db.set_moment_place(conn, moment_id, recognised_place_id, True)
        return recognised_place_id

    place_id = matching_place(conn, moment_id, lat, lon)

    if place_id is None and not db.captioned_frames(conn, moment_id):
        return None

    if place_id is None:
        place_id = db.save_place(conn, None, None, lat, lon)

    map_is_new = has_map and db.place_map(conn, place_id) is None

    if map_is_new:
        db.save_place(conn, place_id, keep_map(place_id, world_map), lat, lon)

    db.set_moment_place(conn, moment_id, place_id, map_is_new)

    return place_id


def describe_frames(frames: list[dict]) -> str:
    lines = []

    for number, frame in enumerate(frames):
        where = ""

        if frame["position"]:
            x, _, z, _ = frame["position"]
            where = f" at x {x:.1f}, z {z:.1f}"

        lines.append(f"{number}. {frame['offset_seconds']:.0f} s{where}: {frame['summary']}")

    return "\n".join(lines)


def ask_for_labels(conn, moment_id: str, frames: list[dict], known: list[dict]) -> Labels | None:
    """Ask a model which room every frame was taken in."""
    said = "\n".join(f"{line['start']:.0f} s: {line['text']}" for line in db.transcript_lines(conn, moment_id))
    known_rooms = "\n".join(
        f"- {room['name']}: {room['description'] or ''} Seen there: " + " | ".join(db.room_examples(conn, room["id"]))
        for room in known
    )
    scan = scan_note(MOMENTS_DIR / moment_id)

    prompt = (
        f"Known rooms of this place:\n{known_rooms or '(none yet)'}\n\n"
        f"Scan: {scan or 'no lidar scan'}\n\n"
        f"What was said:\n{said or '(nothing)'}\n\n"
        f"Frames:\n{describe_frames(frames)}"
    )

    llm.require_price(SUMMARY_MODEL)
    response = llm.client().responses.parse(
        model=SUMMARY_MODEL,
        instructions=INSTRUCTIONS,
        input=prompt,
        text_format=Labels,
        reasoning={"effort": "medium"},
    )
    llm.log_cost(conn, "rooms", SUMMARY_MODEL, response.usage)

    return response.output_parsed


def label_rooms(conn, moment_id: str) -> dict[str, int]:
    """Decide the room of every frame in a moment, name the place if it has no name, and return frames per room."""
    storage.fetch_folder(MOMENTS_DIR / moment_id)
    place = db.moment_place(conn, moment_id)
    frames = db.captioned_frames(conn, moment_id)

    if place is None or not frames:
        return {}

    labels = ask_for_labels(conn, moment_id, frames, db.place_rooms(conn, place["id"]))

    if labels is None:
        return {}

    if not place["name"]:
        db.name_place(conn, place["id"], capitalised(labels.place), by_user=False)

    descriptions = {room.name.strip().lower(): room.description for room in labels.rooms}
    room_by_frame_number = {
        item.frame: capitalised(item.room) for item in labels.frames if 0 <= item.frame < len(frames)
    }
    room_ids = {
        name.lower(): db.save_room(conn, place["id"], name, descriptions.get(name.lower()))
        for name in set(room_by_frame_number.values())
    }

    labelled_frames = []

    for number, frame in enumerate(frames):
        if number in room_by_frame_number:
            x, z = (frame["position"][0], frame["position"][2]) if frame["position"] else (None, None)
            labelled_frames.append((frame["id"], x, z, room_ids[room_by_frame_number[number].lower()]))

    mapped_rooms = db.mapped_frames(conn, place["id"], moment_id) if place["in_map"] else []
    room_by_frame = smooth(labelled_frames, mapped_rooms)

    db.set_frame_rooms(conn, moment_id, room_by_frame)
    db.drop_unused_rooms_and_places(conn)

    room_names = {room_id: name for name, room_id in room_ids.items()}

    return dict(Counter(room_names.get(room_id, "other") for room_id in room_by_frame.values()))


def rebuild(conn) -> None:
    """Put every moment at a place again, oldest first, as if each had just been recorded, and relabel its rooms."""
    db.forget_unnamed_places(conn)
    conn.commit()

    for moment_id in db.moment_ids_oldest_first(conn):
        place_id = assign_place(conn, moment_id, MOMENTS_DIR / moment_id, None)
        counts = label_rooms(conn, moment_id)
        build_facts(conn, moment_id)
        conn.commit()

        print(f"{moment_id}: place {place_id}, rooms {counts}")


def main() -> int:
    parser = argparse.ArgumentParser(description="See and rebuild places and the rooms of every frame.")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("list", help="list places and their rooms")
    label = commands.add_parser("label", help="label the rooms of one moment again")
    label.add_argument("moment_id")
    commands.add_parser("rebuild", help="put every moment at a place again and relabel all rooms")
    args = parser.parse_args()

    with db.connect() as conn:
        if args.command == "list":
            for place in db.list_places(conn):
                print(
                    f"{place['id']}: {place['name'] or '(unnamed)'}, {place['moments']} moments, "
                    f"{'map' if place['has_map'] else 'no map'}: {', '.join(place['rooms'])}"
                )
        elif args.command == "label":
            print(label_rooms(conn, args.moment_id))
            build_facts(conn, args.moment_id)
            conn.commit()
        else:
            rebuild(conn)

    return 0


if __name__ == "__main__":
    sys.exit(main())
