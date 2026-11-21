"""People and their faces."""


import psycopg
import cv2
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response

from memory import db, storage
from memory.config import DATA_DIR
from memory.jobs import start_rebuild
from memory.api.shared import Merge, PersonName, connection

router = APIRouter()


FACE_MARGIN = 0.35


def person_json(person: dict) -> dict:
    """Return a person in the shape the app reads."""
    return {**person, "face": f"/faces/{person['face_id']}" if person["face_id"] else None}


@router.get("/people")
def people(conn: psycopg.Connection = Depends(connection)) -> list[dict]:
    """List everyone whose face was found, most seen first."""
    return [person_json(person) for person in db.list_people(conn)]


@router.put("/people/{person_id}")
def name_person(person_id: int, body: PersonName,
                conn: psycopg.Connection = Depends(connection)) -> dict:
    """Name or rename a person, then rebuild the facts they appear in."""
    name = body.name.strip() if body.name and body.name.strip() else None

    if not db.name_person(conn, person_id, name):
        raise HTTPException(404, "no such person")

    conn.commit()
    start_rebuild(conn, db.person_moments(conn, person_id))

    return {"id": person_id, "name": name}


@router.post("/people/{person_id}/merge")
def merge_person(person_id: int, body: Merge,
                 conn: psycopg.Connection = Depends(connection)) -> dict:
    """Treat two people as the same person."""
    if person_id == body.into or not db.person_moments(conn, body.into):
        raise HTTPException(422, "merge into a different person who exists")

    moments = db.person_moments(conn, person_id)
    db.merge_people(conn, person_id, body.into)
    conn.commit()
    start_rebuild(conn, moments)

    return {"merged": person_id, "into": body.into}


@router.delete("/people/{person_id}")
def forget_person(person_id: int, conn: psycopg.Connection = Depends(connection)) -> dict:
    """Delete a person and every face of them."""
    moments = db.person_moments(conn, person_id)

    if not db.forget_person(conn, person_id):
        raise HTTPException(404, "no such person")

    conn.commit()
    start_rebuild(conn, moments)

    return {"forgotten": person_id}


@router.get("/faces/{face_id}")
def face(face_id: int, conn: psycopg.Connection = Depends(connection)) -> Response:
    """Return one face, cropped from its photo with a little margin, as JPEG."""
    found = db.face_crop(conn, face_id)
    photo = storage.local(DATA_DIR / found[0]) if found else None
    image = cv2.imread(str(photo)) if photo else None

    if image is None:
        raise HTTPException(404, "no such face")

    _, x, y, w, h = found
    height, width = image.shape[:2]
    left, top = max(0, int((x - w * FACE_MARGIN) * width)), max(0, int((y - h * FACE_MARGIN) * height))
    right, bottom = int((x + w * (1 + FACE_MARGIN)) * width), int((y + h * (1 + FACE_MARGIN)) * height)

    _, jpeg = cv2.imencode(".jpg", image[top:bottom, left:right])

    return Response(jpeg.tobytes(), media_type="image/jpeg")
