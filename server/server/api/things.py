"""Things seen more than once: the links made between them, to check, confirm or undo."""

import psycopg
from fastapi import APIRouter, Depends, Query, HTTPException

from server import db
from server.api.shared import connection, image_url

router = APIRouter()


def side_json(side: dict) -> dict:
    return {"id": side["id"], "name": side["name"], "image": image_url(side["image_path"]), "box": side["box"]}


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
