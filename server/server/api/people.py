"""People and their faces."""

import psycopg
import cv2
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response

from server import db, renames, storage
from server.config import DATA_DIR
from server.jobs import start_rebuild
from server.api.shared import Merge, PersonName, connection, image_url

router = APIRouter()


FACE_CROP_MARGIN = 0.35


def person_json(person: dict) -> dict:
    """Return a person in the shape the app reads."""
    return {**person, "face": f"/faces/{person['face_id']}" if person["face_id"] else None}


@router.get("/people")
def people(conn: psycopg.Connection = Depends(connection)) -> list[dict]:
    """List everyone whose face was found, most seen first."""
    return [person_json(person) for person in db.list_people(conn)]


@router.get("/people/{person_id}")
def person(person_id: int, conn: psycopg.Connection = Depends(connection)) -> dict:
    """Return a person with a photo from each scene they are in and the recordings they are in."""
    found = next((person for person in db.list_people(conn) if person["id"] == person_id), None)

    if not found:
        raise HTTPException(404, "no such person")

    photos = [
        {**{key: photo[key] for key in ("id", "at", "moment_id", "offset_seconds")}, "image": image_url(photo["image_path"])}
        for photo in db.person_photos(conn, person_id)
    ]

    return {**person_json(found), "photos": photos, "recordings": db.person_recordings(conn, person_id)}


@router.put("/people/{person_id}")
def name_person(person_id: int, body: PersonName, conn: psycopg.Connection = Depends(connection)) -> dict:
    """Name or rename a person, then rebuild the facts they appear in."""
    name = body.name.strip() if body.name and body.name.strip() else None
    exists, old = db.person_name(conn, person_id)

    if not exists:
        raise HTTPException(404, "no such person")

    db.name_person(conn, person_id, name)

    if old and name and old != name:
        renames.rename_person(conn, db.person_moments(conn, person_id), person_id, old, name)
        conn.commit()
    elif old != name:
        conn.commit()
        start_rebuild(conn, db.person_moments(conn, person_id))

    return {"id": person_id, "name": name}


@router.post("/people/{person_id}/merge")
def merge_person(person_id: int, body: Merge, conn: psycopg.Connection = Depends(connection)) -> dict:
    """Treat two people as the same person."""
    if person_id == body.into or not db.person_moments(conn, body.into):
        raise HTTPException(422, "merge into a different person who exists")

    (_, name), (_, into_name) = db.person_name(conn, person_id), db.person_name(conn, body.into)
    moments, into_moments = db.person_moments(conn, person_id), db.person_moments(conn, body.into)

    renames.move_person_entity(conn, person_id, body.into)
    db.merge_people(conn, person_id, body.into)

    if name and into_name and name != into_name:
        renames.rename_person(conn, moments, body.into, name, into_name)

    conn.commit()

    if name and not into_name:
        start_rebuild(conn, into_moments)
    elif into_name and not name:
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
    face_row = db.face_crop(conn, face_id)
    photo = storage.local_copy(DATA_DIR / face_row[0]) if face_row else None
    image = cv2.imread(str(photo)) if photo else None

    if image is None:
        raise HTTPException(404, "no such face")

    _, x, y, w, h = face_row
    height, width = image.shape[:2]
    left, top = max(0, int((x - w * FACE_CROP_MARGIN) * width)), max(0, int((y - h * FACE_CROP_MARGIN) * height))
    right, bottom = int((x + w * (1 + FACE_CROP_MARGIN)) * width), int((y + h * (1 + FACE_CROP_MARGIN)) * height)

    _, jpeg = cv2.imencode(".jpg", image[top:bottom, left:right])

    return Response(jpeg.tobytes(), media_type="image/jpeg", headers={"Cache-Control": "private, max-age=31536000, immutable"})
