"""Rooms the phone has mapped, recognised again across moments, and named."""

import shutil

from pydantic import BaseModel, Field

from memory import db, llm
from memory.config import DATA_DIR, SUMMARY_MODEL
from memory.places import distance_m

ROOMS_DIR = DATA_DIR / "rooms"
NEARBY_METRES = 150

NAME_INSTRUCTIONS = """\
You are given descriptions of what a camera saw in one room. Name the room in one to three plain words, the way a
person would say it (for example "Kitchen", "Living room", "Bedroom", "Office", "Classroom")."""


class RoomName(BaseModel):
    name: str = Field(description="The room's name, one to three words")


def nearby(conn, lat: float | None, lon: float | None) -> list[dict]:
    """Return rooms near a location, most recently updated first; all rooms when the location is unknown."""
    rooms = db.rooms(conn)

    if lat is None or lon is None:
        return rooms

    return [room for room in rooms
            if room["lat"] is None or distance_m(lat, lon, room["lat"], room["lon"]) <= NEARBY_METRES]


def suggest_name(conn, moment_id: str) -> str | None:
    """Ask a model to name the room from what was seen in a moment."""
    summaries = [frame["summary"] for frame in db.captioned_frames(conn, moment_id)][:12]

    if not summaries:
        return None

    llm.require_price(SUMMARY_MODEL)
    response = llm.client().responses.parse(
        model=SUMMARY_MODEL,
        instructions=NAME_INSTRUCTIONS,
        input="\n".join(f"- {summary}" for summary in summaries),
        text_format=RoomName,
        reasoning={"effort": "low"},
    )
    llm.log_cost(conn, "room-name", SUMMARY_MODEL, response.usage)

    return response.output_parsed.name.strip() if response.output_parsed else None


def file_moment(conn, moment_id: str, moment_dir, recognised_room: int | None) -> int | None:
    """Store a moment's world map as its room's latest map, creating and naming a room if it was new."""
    world_map = moment_dir / "world.map"

    if not world_map.is_file():
        return None

    _, lat, lon = db.moment_info(conn, moment_id)
    room_id = db.save_room(conn, recognised_room, "", lat, lon)

    ROOMS_DIR.mkdir(parents=True, exist_ok=True)
    target = ROOMS_DIR / f"{room_id}.map"
    shutil.copy2(world_map, target)

    db.save_room(conn, room_id, f"rooms/{room_id}.map", lat, lon)
    db.set_moment_room(conn, moment_id, room_id)

    if recognised_room is None:
        db.name_room(conn, room_id, suggest_name(conn, moment_id))

    conn.commit()

    return room_id
