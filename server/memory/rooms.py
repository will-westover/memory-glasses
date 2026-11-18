"""Places (an apartment, a lecture hall) and the rooms inside them, for every frame.

    uv run python -m memory.rooms list
    uv run python -m memory.rooms label <moment id>
    uv run python -m memory.rooms rebuild
"""

import argparse
import math
import shutil
import sys
from collections import Counter
from pathlib import Path

import numpy as np
from pydantic import BaseModel, Field

from memory import db, llm
from memory.config import DATA_DIR, SUMMARY_MODEL
from memory.ingest.facts import build_facts
from memory.ingest.poses import load_poses
from memory.places import distance_m

MAPS_DIR = DATA_DIR / "spaces"
MOMENTS_DIR = DATA_DIR / "moments"
NEARBY_METRES = 150
SAME_PLACE = 0.75
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


def smooth(own: list[tuple], others: list[tuple], metres: float = NEIGHBOUR_METRES) -> dict:
    """Give each placed frame the most common room among frames within `metres` of it, keeping ties as they were."""
    placed = [(x, z, room) for _, x, z, room in own if x is not None] + list(others)
    smoothed = {}

    for frame_id, x, z, room in own:
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
            notes.append(f"The lidar scan spans about {high[0] - low[0]:.0f} by {high[2] - low[2]:.0f} m and is "
                         f"{high[1] - low[1]:.1f} m tall.")

    return " ".join(notes) or None


def moment_vector(conn, moment_id: str) -> np.ndarray | None:
    """Embed a moment's frame descriptions and return their average direction."""
    summaries = [frame["summary"] for frame in db.captioned_frames(conn, moment_id)]

    if not summaries:
        return None

    return unit(np.mean([parse_vector(vector) for vector in llm.embed(conn, summaries, "place")], axis=0))


def nearby(conn, lat, lon) -> list[dict]:
    """Return the places near a location, most recently updated first; all of them when the location is unknown."""
    return [place for place in db.places(conn)
            if lat is None or place["lat"] is None or distance_m(lat, lon, place["lat"], place["lon"]) <= NEARBY_METRES]


def matching_place(conn, moment_id: str, lat, lon) -> int | None:
    """Return the nearby place whose frames look most like this moment's, if they look alike enough."""
    vector = moment_vector(conn, moment_id)

    if vector is None:
        return None

    close = {place["id"] for place in nearby(conn, lat, lon)}
    scores = {place_id: float(unit(parse_vector(text)) @ vector)
              for place_id, text in db.place_vectors(conn).items() if place_id in close}

    best = max(scores, key=scores.get, default=None)

    return best if best is not None and scores[best] >= SAME_PLACE else None


def keep_map(place_id: int, world_map: Path) -> str:
    """Copy a world map into the maps folder and return its path under the data folder."""
    MAPS_DIR.mkdir(parents=True, exist_ok=True)
    shutil.copy2(world_map, MAPS_DIR / f"{place_id}.map")

    return f"spaces/{place_id}.map"


def file_moment(conn, moment_id: str, moment_dir: Path, recognised: int | None) -> int | None:
    """Put a moment at a place: the one the phone recognised, one that looks the same, or a new one."""
    world_map = moment_dir / "world.map"
    poses_file = moment_dir / "poses.json"
    has_map = world_map.is_file() and not (poses_file.is_file() and travelled(load_poses(poses_file)))
    _, lat, lon = db.moment_info(conn, moment_id)

    if recognised is not None and has_map:
        db.save_place(conn, recognised, keep_map(recognised, world_map), lat, lon)
        db.set_moment_place(conn, moment_id, recognised, True)
        return recognised

    place_id = matching_place(conn, moment_id, lat, lon)

    if place_id is None and not db.captioned_frames(conn, moment_id):
        return None

    if place_id is None:
        place_id = db.save_place(conn, None, None, lat, lon)

    first_map = has_map and db.place_map(conn, place_id) is None

    if first_map:
        db.save_place(conn, place_id, keep_map(place_id, world_map), lat, lon)

    db.set_moment_place(conn, moment_id, place_id, first_map)

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
    rooms = "\n".join(f"- {room['name']}: {room['description'] or ''} Seen there: "
                      + " | ".join(db.room_examples(conn, room["id"])) for room in known)
    scan = scan_note(MOMENTS_DIR / moment_id)

    prompt = (f"Known rooms of this place:\n{rooms or '(none yet)'}\n\n"
              f"Scan: {scan or 'no lidar scan'}\n\n"
              f"What was said:\n{said or '(nothing)'}\n\n"
              f"Frames:\n{describe_frames(frames)}")

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
    picked = {item.frame: capitalised(item.room) for item in labels.frames if 0 <= item.frame < len(frames)}
    rooms = {name.lower(): db.save_room(conn, place["id"], name, descriptions.get(name.lower()))
             for name in set(picked.values())}

    own = []

    for number, frame in enumerate(frames):
        if number in picked:
            x, z = (frame["position"][0], frame["position"][2]) if frame["position"] else (None, None)
            own.append((frame["id"], x, z, rooms[picked[number].lower()]))

    others = db.mapped_frames(conn, place["id"], moment_id) if place["in_map"] else []
    chosen = smooth(own, others)

    db.set_frame_rooms(conn, moment_id, chosen)
    db.drop_empty(conn)

    names = {room_id: name for name, room_id in rooms.items()}

    return dict(Counter(names.get(room_id, "other") for room_id in chosen.values()))


def rebuild(conn) -> None:
    """Put every moment at a place again, oldest first, as if each had just been recorded, and relabel its rooms."""
    conn.execute("delete from segment")
    conn.execute("update frame set room_id = null")
    conn.execute("delete from room where not named")
    conn.execute("delete from place where not named")
    conn.commit()

    for moment_id in reversed([row[0] for row in conn.execute("select id from moment order by started_at desc")]):
        place_id = file_moment(conn, moment_id, MOMENTS_DIR / moment_id, None)
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
            for place in db.places(conn):
                print(f"{place['id']}: {place['name'] or '(unnamed)'}, {place['moments']} moments, "
                      f"{'map' if place['has_map'] else 'no map'}: {', '.join(place['rooms'])}")
        elif args.command == "label":
            print(label_rooms(conn, args.moment_id))
            build_facts(conn, args.moment_id)
            conn.commit()
        else:
            rebuild(conn)

    return 0


if __name__ == "__main__":
    sys.exit(main())
