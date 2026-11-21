"""Places, the rooms inside them, and the world map."""


import psycopg
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response

from memory import db, places, storage
from memory.config import DATA_DIR
from memory.ingest.poses import load_poses, thin
from memory.jobs import start_rebuild
from memory.moment_files import MOMENTS_DIR
from memory.api.shared import NewName, connection, frame_json, image_url, stored_file

router = APIRouter()


@router.get("/places")
def list_places(lat: float | None = None, lon: float | None = None,
                conn: psycopg.Connection = Depends(connection)) -> list[dict]:
    """List places near a location (or all of them), most recently updated first."""
    return places.nearby(conn, lat, lon)


@router.get("/world")
def world(conn: psycopg.Connection = Depends(connection)) -> dict:
    """Return every place and every moment that has a location, to show on a map."""
    covers = db.covers(conn)

    return {
        "places": [place for place in db.list_places(conn) if place["lat"] is not None],
        "moments": [{**moment, "cover": image_url(covers.get(moment["id"]))} for moment in db.located_moments(conn)],
    }


@router.get("/places/{place_id}")
def place_detail(place_id: int, conn: psycopg.Connection = Depends(connection)) -> dict:
    """Return a place's rooms and the moments recorded there."""
    found = next((place for place in db.list_places(conn) if place["id"] == place_id), None)

    if found is None:
        raise HTTPException(404, "no such place")

    return {**found, "rooms": db.place_rooms(conn, place_id), "moment_ids": db.place_moments(conn, place_id)}


@router.get("/places/{place_id}/scene")
def place_scene(place_id: int, conn: psycopg.Connection = Depends(connection)) -> dict:
    """Return every moment that shares a place's map, with its path and frames, in the map's coordinates."""
    moments = []
    mesh_moment = None

    for moment_id in db.place_moments(conn, place_id):
        poses_file = MOMENTS_DIR / moment_id / "poses.json"

        if not (db.moment_place(conn, moment_id) or {}).get("in_map"):
            continue

        started_at, _, _ = db.moment_info(conn, moment_id)
        moments.append({
            "id": moment_id,
            "started_at": started_at,
            "path": thin(load_poses(poses_file), 300) if storage.local(poses_file) else [],
            "frames": [frame_json(frame) for frame in db.captioned_frames(conn, moment_id)],
        })

        if mesh_moment is None and storage.exists(MOMENTS_DIR / moment_id / "mesh.obj"):
            mesh_moment = moment_id

    return {"id": place_id, "mesh_moment": mesh_moment, "moments": moments}


@router.get("/places/{place_id}/map")
def place_map_file(place_id: int, conn: psycopg.Connection = Depends(connection)) -> Response:
    """Return a place's saved ARKit map, so the phone can recognise it."""
    path = db.place_map(conn, place_id)
    file = (DATA_DIR / path).resolve() if path else None

    if file is None:
        raise HTTPException(404, "no map for this place")

    return stored_file(file, "application/octet-stream")


@router.put("/places/{place_id}")
def rename_place(place_id: int, body: NewName, conn: psycopg.Connection = Depends(connection)) -> dict:
    """Name or rename a place."""
    name = body.name.strip() if body.name and body.name.strip() else None

    if not db.name_place(conn, place_id, name, by_user=True):
        raise HTTPException(404, "no such place")

    return {"id": place_id, "name": name}


@router.put("/rooms/{room_id}")
def rename_room(room_id: int, body: NewName,
                conn: psycopg.Connection = Depends(connection)) -> dict:
    """Rename a room, and rebuild the facts that mention it."""
    name = body.name.strip() if body.name and body.name.strip() else None

    if name is None:
        raise HTTPException(422, "a room needs a name")

    place_id = db.name_room(conn, room_id, name)

    if place_id is None:
        raise HTTPException(404, "no such room")

    conn.commit()
    start_rebuild(conn, db.place_moments(conn, place_id))

    return {"id": room_id, "name": name}
