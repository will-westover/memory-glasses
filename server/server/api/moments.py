"""Moments: the list, one moment's page, and its audio, video, path, mesh and images."""

import io
import math
import re
import wave
from datetime import datetime

import psycopg
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response

from server import db
from server.jobs import jobs
from server.shared import storage
from server.ingest.poses import load_poses, thin
from server.ingest.recording import MAX_FRAMES_PER_MINUTE
from server.shared.gps import place_for
from server.shared.files import MOMENTS_DIR, manifest_of, video_path
from server.ingest.thumbs import thumb_path
from server.api.shared import (
    MAX_CLIP_SECONDS,
    Rate,
    connection,
    fact_json,
    frame_json,
    image_url,
    redirect_to_stored,
    video_type,
)

router = APIRouter()


MOMENTS_PAGE = 50


@router.get("/moments")
def moments(
    before: datetime | None = None,
    limit: int = Query(MOMENTS_PAGE, ge=1, le=500),
    conn: psycopg.Connection = Depends(connection),
) -> list[dict]:
    """List moments newest first, a page at a time: those that started before `before`, if given."""
    places = db.gps_places(conn)
    page = db.list_moments(conn, before, limit)
    covers = db.covers(conn, [row[0] for row in page])

    return [
        {
            "id": moment_id,
            "source": source,
            "started_at": started_at,
            "duration_seconds": duration,
            "frames": frames,
            "has_sound": has_sound,
            "place": place_for(lat, lon, places),
            "cover": image_url(covers.get(moment_id)),
            "title": title,
        }
        for moment_id, source, started_at, duration, frames, has_sound, lat, lon, title in page
    ]


def without_place(text: str) -> str:
    """Return a summary without the room and place the search index adds to its end."""
    return re.sub(r"(\s+(Room:|At) [^.]+\.)+$", "", text)


@router.get("/moments/{moment_id}")
def moment(moment_id: str, conn: psycopg.Connection = Depends(connection)) -> dict:
    """Return one moment's frames, transcript and summaries for the timeline."""
    if not db.moment_start_and_location(conn, moment_id):
        raise HTTPException(404, "no such moment")

    title, overview = db.moment_overview(conn, moment_id)
    summaries = db.summaries(conn, moment_id)
    titles = db.chapter_titles(conn, moment_id)

    return {
        "id": moment_id,
        "title": title,
        "overview": overview,
        "chapters": [
            {
                "id": fact["id"],
                "start": fact["offset_seconds"],
                "end": fact["end_offset_seconds"],
                "title": titles.get(fact["id"]),
                "text": without_place(fact["text"]),
            }
            for fact in summaries
        ],
        "frames": [frame_json(frame) for frame in db.captioned_frames(conn, moment_id)],
        "transcript": db.transcript_lines(conn, moment_id),
        "summaries": [fact_json(fact) for fact in summaries],
        **db.kept_files(conn, moment_id),
        "place": db.moment_place(conn, moment_id),
    }


WAV_HEADER_BYTES = 4096


def wav_layout(header: bytes) -> tuple[int, int, int, int, int]:
    """Read a WAV header: return channels, frame rate, bytes per sample, where the sound starts and how many bytes it has."""
    position, channels, rate, width = 12, 1, 0, 2

    while position + 8 <= len(header):
        name, size = header[position : position + 4], int.from_bytes(header[position + 4 : position + 8], "little")

        if name == b"fmt ":
            channels = int.from_bytes(header[position + 10 : position + 12], "little")
            rate = int.from_bytes(header[position + 12 : position + 16], "little")
            width = int.from_bytes(header[position + 22 : position + 24], "little") // 8
        elif name == b"data":
            return channels, rate, width, position + 8, size

        position += 8 + size + size % 2

    raise ValueError("no sound in this WAV header")


def audio_clip(read, start: float, end: float) -> bytes:
    """Return the part of a WAV file between two times as a WAV file of its own, reading only the bytes it needs."""
    channels, rate, width, data_start, data_size = wav_layout(read(0, WAV_HEADER_BYTES - 1))
    block = channels * width
    frames = data_size // block
    first = min(max(0, int(start * rate)), frames)
    last = min(max(first, int(end * rate)), frames)
    sound = read(data_start + first * block, data_start + last * block - 1) if last > first else b""

    out = io.BytesIO()

    with wave.open(out, "wb") as clip:
        clip.setnchannels(channels)
        clip.setsampwidth(width)
        clip.setframerate(rate)
        clip.writeframes(sound)

    return out.getvalue()


@router.get("/moments/{moment_id}/audio")
def audio(moment_id: str, start: float, end: float) -> Response:
    """Return what was heard between two offsets in a moment, as WAV."""
    if not 0 <= start < end or end - start > MAX_CLIP_SECONDS:
        raise HTTPException(422, f"start and end must be 0 or more and at most {MAX_CLIP_SECONDS} s apart")

    file = (MOMENTS_DIR / moment_id / "audio.wav").resolve()

    if not file.is_relative_to(MOMENTS_DIR) or not storage.exists(file):
        raise HTTPException(404, "no sound for this moment")

    return Response(audio_clip(lambda first, last: storage.read_range(file, first, last), start, end), media_type="audio/wav")


@router.get("/moments/{moment_id}/path")
def moment_path(moment_id: str) -> dict:
    """Return where the phone went during a moment: [seconds, x, y, z, yaw] in metres and radians."""
    poses_file = (MOMENTS_DIR / moment_id / "poses.json").resolve()

    if not poses_file.is_relative_to(MOMENTS_DIR) or storage.local_copy(poses_file) is None:
        return {"points": []}

    return {"points": thin(load_poses(poses_file))}


@router.get("/moments/{moment_id}/mesh")
def moment_mesh(moment_id: str) -> Response:
    """Return the mesh the phone's lidar captured during a moment, as OBJ."""
    mesh = (MOMENTS_DIR / moment_id / "mesh.obj").resolve()

    if not mesh.is_relative_to(MOMENTS_DIR):
        raise HTTPException(404, "no mesh for this moment")

    return redirect_to_stored(mesh, "model/obj")


def check_higher(moment_id: str, frames_per_minute: float) -> None:
    """Refuse a rate that isn't higher than the moment's current one: frames already paid for are kept."""
    current = manifest_of(moment_id).get("frames_per_minute") or 0

    if not current < frames_per_minute <= MAX_FRAMES_PER_MINUTE:
        raise HTTPException(422, f"choose more than {current:g} and at most {MAX_FRAMES_PER_MINUTE} frames a minute")


@router.get("/moments/{moment_id}/reprocess-estimate")
def reprocess_estimate(
    moment_id: str, frames_per_minute: float, conn: psycopg.Connection = Depends(connection)
) -> dict:
    """Say how many new frames a rate would add and what describing them would cost."""
    path = video_path(moment_id)

    if path is None:
        raise HTTPException(404, "this moment's video wasn't kept, so it can't be reprocessed")

    check_higher(moment_id, frames_per_minute)

    manifest = manifest_of(moment_id)
    slots = math.ceil(manifest.get("duration_seconds", 0) * frames_per_minute / 60)
    kept = min(len(manifest.get("frames", [])), slots)

    return {
        "frames": slots,
        "new_frames": slots - kept,
        "kept_captions": kept,
        "usd": round((slots - kept) * per_frame, 4) if (per_frame := db.average_caption_cost(conn)) is not None else None,
    }


@router.post("/moments/{moment_id}/reprocess")
def reprocess(moment_id: str, body: Rate, conn: psycopg.Connection = Depends(connection)) -> dict:
    """Rebuild a moment at a different frames per minute, in the background."""
    if video_path(moment_id) is None:
        raise HTTPException(404, "this moment's video wasn't kept, so it can't be reprocessed")

    check_higher(moment_id, body.frames_per_minute)
    task = {"kind": "reprocess", "moment_id": moment_id, "frames_per_minute": body.frames_per_minute}
    job_id = db.new_job(conn, body.frames_per_minute, task)
    db.update_job(conn, job_id, moment_id=moment_id, step="Waiting")
    jobs.start_job(conn, job_id)

    return {"id": job_id, "status": "processing"}


@router.get("/moments/{moment_id}/video")
def moment_video(moment_id: str) -> Response:
    """Stream a moment's original video."""
    path = video_path(moment_id, download=False)

    if path is None:
        raise HTTPException(404, "no video for this moment")

    return redirect_to_stored(path, video_type(path))


@router.get("/moments/{moment_id}/video/link")
def moment_video_link(moment_id: str) -> dict:
    """Return a short-lived link that plays a moment's video straight from R2."""
    path = video_path(moment_id, download=False)

    if path is None:
        raise HTTPException(404, "no video for this moment")

    return {"url": storage.download_url(path, video_type(path), seconds=3600)}


@router.get("/images/{path:path}")
def image(path: str, size: str | None = None) -> Response:
    """Serve one frame's JPEG, or its small copy when size is small."""
    file = (MOMENTS_DIR / path).resolve()

    if not file.is_relative_to(MOMENTS_DIR) or file.suffix != ".jpg":
        raise HTTPException(404, "no such image")

    return redirect_to_stored(thumb_path(file) if size == "small" else file, "image/jpeg")
