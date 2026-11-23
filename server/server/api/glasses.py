"""The glasses: photos arriving, recordings ending, firmware and status."""

import json
import pathlib
from datetime import datetime

import psycopg
from fastapi import APIRouter, Depends, Header, HTTPException, Request
from pydantic import BaseModel, Field

from server import db
from server.ingest import glasses
from server.jobs import jobs
from server.shared import storage
from server.shared.config import DATA_DIR
from server.api.shared import RECORDING_ID_PATTERN, connection, current_settings

router = APIRouter()

PHOTOS_MISSED_BEFORE_QUIET = 3


@router.post("/glasses/frames")
async def glasses_frame(
    request: Request,
    x_recording: str = Header(...),
    x_uploaded: str | None = Header(default=None),
    x_taken: float | None = Header(default=None),
    conn: psycopg.Connection = Depends(connection),
) -> dict:
    """Take one frame from the glasses, sent here or put in R2 first, and reply with the settings and the next link."""
    if not RECORDING_ID_PATTERN.match(x_recording):
        raise HTTPException(422, "X-Recording must be letters, digits and dashes")

    if x_uploaded is not None:
        if not glasses.UPLOAD_NAME_PATTERN.match(x_uploaded):
            raise HTTPException(422, "X-Uploaded must name an upload link this server gave out")

        path = glasses.accept_uploaded_frame(conn, x_recording, x_uploaded, taken=x_taken)

        if path is None:
            raise HTTPException(404, "that frame isn't in storage, or is over 2 MB")
    else:
        jpeg = await request.body()

        if not jpeg.startswith(b"\xff\xd8") or len(jpeg) > glasses.MAX_FRAME_BYTES:
            raise HTTPException(422, "the body must be one JPEG under 2 MB")

        path = glasses.save_frame(conn, x_recording, jpeg, taken=x_taken)

    if db.end_older_recordings(conn, x_recording):
        conn.commit()
        jobs.process_ended_recordings()

    return {
        "recording": x_recording,
        "frame": path.name,
        **current_settings(conn).model_dump(),
        **glasses.upload_slot(x_recording),
    }


FIRMWARE_DIR = DATA_DIR / "firmware"


def latest_firmware() -> dict | None:
    """Return the newest published glasses firmware's details, read fresh from R2."""
    try:
        return json.loads(
            storage.bucket().Object(storage.bucket_key(FIRMWARE_DIR / "latest.json")).get()["Body"].read()
        )
    except Exception as error:
        if storage.is_not_found(error):
            return None
        raise


@router.post("/glasses/recordings/{recording}/end")
def end_recording(recording: str, conn: psycopg.Connection = Depends(connection)) -> dict:
    """Say a glasses recording is over, which starts processing it."""
    if not RECORDING_ID_PATTERN.match(recording):
        raise HTTPException(422, "the recording id must be letters, digits and dashes")

    ended = db.end_recording(conn, recording)
    conn.commit()

    return {"ended": ended, "started": jobs.process_ended_recordings() if ended else []}


@router.get("/glasses/firmware")
def glasses_firmware() -> dict:
    """Say which glasses firmware is newest, with a short-lived link to download it."""
    firmware = latest_firmware()

    if firmware is None:
        raise HTTPException(404, "no firmware published")

    firmware_path = FIRMWARE_DIR / pathlib.PurePosixPath(firmware["key"]).name
    url = storage.download_url(firmware_path, "application/octet-stream", seconds=3600)

    return {"version": firmware["version"], "size": firmware["size"], "crc32": firmware["crc32"], "url": url}


class PhonePoint(BaseModel):
    at: datetime
    lat: float = Field(ge=-90, le=90)
    lon: float = Field(ge=-180, le=180)
    accuracy_m: float | None = Field(None, ge=0)


class PhoneTrail(BaseModel):
    points: list[PhonePoint]


@router.post("/locations")
def phone_locations(body: PhoneTrail, conn: psycopg.Connection = Depends(connection)) -> dict:
    """Keep where the phone was while the glasses were connected, so glasses recordings know where they were."""
    db.save_phone_locations(conn, [(point.at, point.lat, point.lon, point.accuracy_m) for point in body.points])
    conn.commit()

    return {"saved": len(body.points)}


@router.get("/glasses/status")
def glasses_status(conn: psycopg.Connection = Depends(connection)) -> dict:
    """Say whether the glasses are sending, how many photos the current recording has, and what is processing."""
    latest = db.latest_recording(conn)
    rate = current_settings(conn).frames_per_minute
    expected_gap = 60 / rate if rate else None

    return {
        "sending": latest is not None
        and expected_gap is not None
        and latest["seconds_since_last"] < PHOTOS_MISSED_BEFORE_QUIET * expected_gap,
        "photos": latest["photos"] if latest else 0,
        "seconds_since_last": latest["seconds_since_last"] if latest else None,
        "processing": db.count_processing_jobs(conn),
    }
