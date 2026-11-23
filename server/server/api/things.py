"""Things seen more than once: the links made between them, to check, confirm or undo."""

import psycopg
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response

from server import db
from server.api.shared import connection, image_url
from server.ingest.link import crop_jpeg

router = APIRouter()


def side_json(side: dict) -> dict:
    return {
        "id": side["id"],
        "name": side["name"],
        "image": image_url(side["image_path"]),
        "box": side["box"],
        "photo": f"/things/{side['id']}/photo",
    }


LINKS_PAGE = 50


@router.get("/things/links")
def thing_links(
    before: int | None = None, limit: int = Query(LINKS_PAGE, ge=1, le=500), conn: psycopg.Connection = Depends(connection)
) -> list[dict]:
    """List links between things newest first, a page at a time, each with both photos and the reason."""
    return [
        {
            **side_json(link),
            "same_as": side_json(link["same_as"]),
            "reason": link["reason"],
            "confirmed": link["confirmed"],
        }
        for link in db.recent_links(conn, limit, before)
    ]


@router.post("/things/{entity_id}/confirm")
def confirm_thing_link(entity_id: int, conn: psycopg.Connection = Depends(connection)) -> dict:
    """Say a link is right."""
    if not db.confirm_link(conn, entity_id):
        raise HTTPException(404, "no linked thing with that id")

    conn.commit()

    return {"id": entity_id, "confirmed": True}


@router.post("/things/{entity_id}/unlink")
def unlink_thing(entity_id: int, conn: psycopg.Connection = Depends(connection)) -> dict:
    """Say a link is wrong, so the thing stands on its own again."""
    if not db.unlink_thing(conn, entity_id):
        raise HTTPException(404, "no linked thing with that id")

    conn.commit()

    return {"id": entity_id, "linked": False}


@router.get("/things/{entity_id}/photo")
def thing_photo(entity_id: int, conn: psycopg.Connection = Depends(connection)) -> Response:
    """Return the part of a photo that shows a thing, or the whole photo when it has no box, as JPEG."""
    found = db.thing_photo(conn, entity_id)

    if found is None:
        raise HTTPException(404, "no photo of that thing")

    image_path, box = found
    jpeg = crop_jpeg(image_path, box or [0.0, 0.0, 1.0, 1.0])

    if jpeg is None:
        raise HTTPException(404, "no photo of that thing")

    return Response(jpeg, media_type="image/jpeg", headers={"Cache-Control": "private, max-age=31536000, immutable"})
