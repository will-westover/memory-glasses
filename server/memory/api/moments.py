"""Moments: the list, one moment's page, and its audio, video, path, mesh and images."""

import io
import math
import re
import wave

import psycopg
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response

from memory import db, jobs, storage
from memory.ingest.poses import load_poses, thin
from memory.ingest.recording import MAX_FRAMES_PER_MINUTE
from memory.gps import place_for
from memory.moment_files import MOMENTS_DIR, manifest_of, video_path
from memory.thumbs import thumb_path
from memory.api.shared import (
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


@router.get("/moments")
def moments(conn: psycopg.Connection = Depends(connection)) -> list[dict]:
    """List every moment, newest first."""
    places = db.gps_places(conn)
    covers = db.covers(conn)

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
        for moment_id, source, started_at, duration, frames, has_sound, lat, lon, title in db.list_moments(conn)
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


def audio_clip(path, start: float, end: float) -> bytes:
    """Return the part of a WAV file between two times as a WAV file of its own."""
    with wave.open(str(path)) as source:
        rate = source.getframerate()
        first = min(max(0, int(start * rate)), source.getnframes())
        last = min(max(first, int(end * rate)), source.getnframes())

        source.setpos(first)
        frames = source.readframes(last - first)
        params = source.getparams()

    out = io.BytesIO()

    with wave.open(out, "wb") as clip:
        clip.setparams(params)
        clip.writeframes(frames)

    return out.getvalue()


@router.get("/moments/{moment_id}/audio")
def audio(moment_id: str, start: float, end: float) -> Response:
    """Return what was heard between two offsets in a moment, as WAV."""
    if not 0 <= start < end or end - start > MAX_CLIP_SECONDS:
        raise HTTPException(422, f"start and end must be 0 or more and at most {MAX_CLIP_SECONDS} s apart")

    file = (MOMENTS_DIR / moment_id / "audio.wav").resolve()

    if not file.is_relative_to(MOMENTS_DIR) or storage.local_copy(file) is None:
        raise HTTPException(404, "no sound for this moment")

    return Response(audio_clip(file, start, end), media_type="audio/wav")


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
        "usd": round((slots - kept) * db.average_caption_cost(conn), 4),
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
