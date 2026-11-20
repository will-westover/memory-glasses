"""The HTTP API the iPhone app talks to."""

import io
import json
import math
import os
import pathlib
import re
import shutil
import wave
from datetime import datetime
from uuid import UUID
from collections.abc import Iterator

import psycopg
import cv2
from fastapi.encoders import jsonable_encoder
from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.responses import RedirectResponse, Response, StreamingResponse
from pydantic import BaseModel, Field

from memory import chat, db, glasses, pipeline, rooms, storage
from memory.agent import answer_events, ask_agent, fact_record
from memory.config import DATA_DIR, FRAMES_PER_MINUTE, STRETCH_SECONDS
from memory.evals import RUNS_DIR
from memory.ingest.facts import build_facts, first_sentence
from memory.ingest.poses import load_poses, thin
from memory.ingest.recording import MAX_FRAMES_PER_MINUTE
from memory.places import place_for
from memory.search import search
from memory.tasks import start as start_job

MOMENTS_DIR = (DATA_DIR / "moments").resolve()
MAX_CLIP_SECONDS = 120
UPLOADS_DIR = DATA_DIR / "uploads"
EXTRA_FILES = {"poses": "poses.json", "mesh": "mesh.obj", "world_map": "world.map"}
RECORDING_ID = re.compile(r"^[A-Za-z0-9-]{1,40}$")
MAX_FRAME_BYTES = 2_000_000
MIN_DISPLAY_SCALE = 0.3
SENDING_SECONDS = 20


class ChatQuestion(BaseModel):
    question: str = Field(min_length=1)
    frame_id: int | None = None


class NewName(BaseModel):
    name: str | None = Field(default=None, max_length=60)


class Rate(BaseModel):
    frames_per_minute: float = Field(ge=1, le=MAX_FRAMES_PER_MINUTE)


class PersonName(BaseModel):
    name: str | None = Field(default=None, max_length=80)


class Merge(BaseModel):
    into: int


class Settings(BaseModel):
    frames_per_minute: float = Field(FRAMES_PER_MINUTE, ge=0, le=MAX_FRAMES_PER_MINUTE)
    display_scale: float = Field(1.0, ge=MIN_DISPLAY_SCALE, le=1)
    display_x: float = Field(0.0, ge=-1, le=1)
    display_y: float = Field(0.0, ge=-1, le=1)
    display_brightness: float = Field(1.0, ge=0.05, le=1)
    display_large_text: bool = False
    display_mirror: bool = False
    display_flip: bool = False
    display_pattern: bool = False
    recording: bool = True


class SettingsChange(BaseModel):
    frames_per_minute: float | None = Field(None, ge=0, le=MAX_FRAMES_PER_MINUTE)
    display_scale: float | None = Field(None, ge=MIN_DISPLAY_SCALE, le=1)
    display_x: float | None = Field(None, ge=-1, le=1)
    display_y: float | None = Field(None, ge=-1, le=1)
    display_brightness: float | None = Field(None, ge=0.05, le=1)
    display_large_text: bool | None = None
    display_mirror: bool | None = None
    display_flip: bool | None = None
    display_pattern: bool | None = None
    recording: bool | None = None


def check_token(authorization: str | None = Header(default=None)) -> None:
    """Refuse the request unless it carries the token set in API_TOKEN."""
    token = os.environ.get("API_TOKEN")

    if not token or authorization != f"Bearer {token}":
        raise HTTPException(401, "missing or wrong token")


def connection() -> Iterator[psycopg.Connection]:
    """Lend one request a connection from the shared pool."""
    with db.pool().connection() as conn:
        yield conn


def check_glasses() -> list[str]:
    """Start processing every glasses recording that has stopped and has no job yet, and return their names."""
    started = []

    for folder in glasses.finished():
        with db.connect() as conn:
            rate = float(db.setting(conn, "frames_per_minute", FRAMES_PER_MINUTE))
            job_id = db.new_job(conn, rate, {"kind": "glasses", "recording": folder.name, "frames_per_minute": rate})
            conn.commit()

        if job_id is not None:
            start_job(job_id)
            started.append(folder.name)

    return started


def process_glasses(job_id, task: dict) -> None:
    """Turn a stopped glasses recording into a video and process it like any upload."""
    folder = glasses.GLASSES_DIR / task["recording"]

    try:
        video = glasses.build_video(folder)
    except Exception as error:
        with db.connect() as conn:
            db.update_job(conn, job_id, status="failed", error=str(error))

        (folder / "video.mp4").touch()
        storage.save(folder / "video.mp4")
        return

    run_upload(job_id, video, {}, task["frames_per_minute"], None, "glasses")


app = FastAPI(title="Memory Glasses", dependencies=[Depends(check_token)])


def image_url(image_path: str | None) -> str | None:
    """Return the URL the app loads a frame from."""
    return f"/images/{image_path.removeprefix('moments/')}" if image_path else None


SPEECH_KINDS = ("speech", "said")


def clip_url(fact: dict, end: float | None) -> str | None:
    """Return the URL of the sound behind a speech fact, if it has one."""
    if fact["kind"] not in SPEECH_KINDS or end is None or end <= fact["offset_seconds"]:
        return None

    start = fact["offset_seconds"]

    return f"/moments/{fact['moment_id']}/audio?start={start:.2f}&end={min(end, start + MAX_CLIP_SECONDS):.2f}"


def fact_json(fact: dict, details: dict | None = None) -> dict:
    """Return a fact in the shape the app reads, with who and where when the details are given."""
    details = details or {}
    speech = fact["kind"] in SPEECH_KINDS
    end = details.get("end_seconds") if speech else None

    if fact["kind"] == "summary":
        end = fact["offset_seconds"] + STRETCH_SECONDS

    return {
        "id": fact["id"],
        "kind": fact["kind"],
        "at": fact["at"],
        "moment_id": fact["moment_id"],
        "offset_seconds": fact["offset_seconds"],
        "end_seconds": end,
        "text": fact["text"],
        "image": image_url(fact["image_path"]),
        "audio_url": clip_url(fact, end),
        "speaker": details.get("speaker") if speech else None,
        "people": details.get("people") or [],
        "place": details.get("place"),
    }


def position_json(pose) -> dict | None:
    """Return a frame's position as named fields, or None."""
    return dict(zip(("x", "y", "z", "yaw"), pose)) if pose else None


def db_duration(conn: psycopg.Connection, moment_id: str) -> float:
    """Return how long a moment lasts, in seconds."""
    return float(conn.execute("select duration_seconds from moment where id = %s", (moment_id,)).fetchone()[0])


def frame_json(frame: dict) -> dict:
    """Return a captioned frame in the shape the app reads."""
    return {
        "id": frame["id"],
        "at": frame["at"],
        "offset_seconds": frame["offset_seconds"],
        "image": image_url(frame["image_path"]),
        "summary": frame["summary"],
        "short": first_sentence(frame["summary"]) if frame["summary"] else None,
        "objects": [{"name": name, "location": location} for name, location in frame["objects"]],
        "texts": [{"text": text, "surface": surface} for text, surface in frame["texts"]],
        "people": [
            {"id": person["id"], "name": person["name"], "face": f"/faces/{person['face_id']}"}
            for person in frame["people"]
        ],
        "position": position_json(frame["position"]),
        "room": frame["room"],
    }


def latest_eval() -> dict | None:
    """Return the scores of the newest eval run, if there is one."""
    runs = sorted(RUNS_DIR.glob("*.json"))

    if not runs:
        return None

    run = json.loads(runs[-1].read_text())

    return {"run": runs[-1].stem, "model": run["model"], "judge": run["judge"], "scores": run["scores"]}


@app.get("/search")
def search_memories(q: str = Query(min_length=1), limit: int = Query(40, ge=1, le=100),
                    conn: psycopg.Connection = Depends(connection)) -> dict:
    """Return the facts that best match some words, best first, for search inside the Timeline."""
    facts = search(conn, q, limit=limit)
    details = db.source_details(conn, facts)

    return {"results": [fact_json(fact, details.get(fact["id"])) for fact in facts]}


@app.get("/moments")
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


@app.get("/moments/{moment_id}")
def moment(moment_id: str, conn: psycopg.Connection = Depends(connection)) -> dict:
    """Return one moment's frames, transcript and summaries for the timeline."""
    if not db.moment_info(conn, moment_id):
        raise HTTPException(404, "no such moment")

    title, overview = db.moment_overview(conn, moment_id)
    summaries = db.summaries(conn, moment_id)
    titles = db.chapter_titles(conn, moment_id)
    ends = [fact["offset_seconds"] for fact in summaries[1:]] + [db_duration(conn, moment_id)]

    return {
        "id": moment_id,
        "title": title,
        "overview": overview,
        "chapters": [{"id": fact["id"], "start": fact["offset_seconds"], "end": end, "title": titles.get(fact["id"]),
                      "text": fact["text"]} for fact, end in zip(summaries, ends)],
        "frames": [frame_json(frame) for frame in db.captioned_frames(conn, moment_id)],
        "transcript": db.transcript_lines(conn, moment_id),
        "summaries": [fact_json(fact) for fact in db.summaries(conn, moment_id)],
        "has_video": has_video(moment_id),
        "frames_per_minute": manifest_of(moment_id).get("frames_per_minute"),
        "has_mesh": storage.exists(MOMENTS_DIR / moment_id / "mesh.obj"),
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


@app.get("/moments/{moment_id}/audio")
def audio(moment_id: str, start: float, end: float) -> Response:
    """Return what was heard between two offsets in a moment, as WAV."""
    if not 0 <= start < end or end - start > MAX_CLIP_SECONDS:
        raise HTTPException(422, f"start and end must be 0 or more and at most {MAX_CLIP_SECONDS} s apart")

    file = (MOMENTS_DIR / moment_id / "audio.wav").resolve()

    if not file.is_relative_to(MOMENTS_DIR) or storage.local(file) is None:
        raise HTTPException(404, "no sound for this moment")

    return Response(audio_clip(file, start, end), media_type="audio/wav")


def stored_file(file, media_type: str | None) -> Response:
    """Send a kept file as a redirect to a short-lived R2 link."""
    return RedirectResponse(storage.download_url(file, media_type), status_code=307)


def run_upload(job_id, video, extras: dict, frames_per_minute: float, place_id: int | None, source: str) -> None:
    """Process an uploaded recording, keeping its job's status up to date."""
    with db.connect() as conn:
        def step(name: str) -> None:
            db.update_job(conn, job_id, step=name)
            conn.commit()

        try:
            storage.fetch_folder(video.parent, videos=True)
            moment_id = pipeline.process(video, frames_per_minute, extras, place_id=place_id, source=source,
                                         on_step=step)
            db.update_job(conn, job_id, status="ready", step=None, moment_id=moment_id)

            if video.parent.parent == UPLOADS_DIR:
                shutil.rmtree(video.parent, ignore_errors=True)
                storage.remove(video.parent)
        except Exception as error:
            conn.rollback()
            db.update_job(conn, job_id, status="failed", error=str(error))


def upload_task(names: dict, frames_per_minute: float, place_id: int | None, source: str) -> dict:
    """Describe the work for an uploaded recording: its files and how to process it."""
    return {"kind": "upload", "video": names["video"], "extras": [name for field, name in names.items() if field != "video"],
            "frames_per_minute": frames_per_minute, "place_id": place_id, "source": source}


def process_upload(job_id, task: dict) -> None:
    """Process the recording an upload job describes."""
    folder = UPLOADS_DIR / str(job_id)
    extras = {name: folder / name for name in task["extras"]}

    run_upload(job_id, folder / task["video"], extras, task["frames_per_minute"], task["place_id"], task["source"])


class DirectUpload(BaseModel):
    frames_per_minute: float = Field(FRAMES_PER_MINUTE, ge=0, le=MAX_FRAMES_PER_MINUTE)
    place_id: int | None = None
    source: str = Field("iphone", pattern="^(iphone|glasses|video)$")
    video_suffix: str = Field(".mov", pattern=r"^\.[a-z0-9]{1,5}$")
    extras: list[str] = Field(default=[], max_length=3)


@app.post("/uploads")
def start_upload(body: DirectUpload, conn: psycopg.Connection = Depends(connection)) -> dict:
    """Start an upload straight to storage: return a link to PUT each file to, valid for an hour."""
    names = {"video": f"video{body.video_suffix}"} | {field: EXTRA_FILES[field] for field in body.extras
                                                      if field in EXTRA_FILES}
    task = upload_task(names, body.frames_per_minute, body.place_id, body.source)
    job_id = db.new_job(conn, body.frames_per_minute, task, status="uploading")
    db.update_job(conn, job_id, step="Uploading")
    conn.commit()

    folder = UPLOADS_DIR / str(job_id)

    return {"id": job_id, "urls": {field: storage.upload_url(folder / name) for field, name in names.items()}}


@app.post("/uploads/{job_id}/done")
def finish_upload(job_id: UUID, conn: psycopg.Connection = Depends(connection)) -> dict:
    """Start processing a recording once its files are in storage."""
    task = db.job_task(conn, job_id)

    if task is None or task.get("kind") != "upload":
        raise HTTPException(404, "no such upload")

    if not storage.exists(UPLOADS_DIR / str(job_id) / task["video"]):
        raise HTTPException(409, "the video hasn't arrived yet")

    db.update_job(conn, job_id, status="processing", step=None)
    conn.commit()
    start_job(job_id)

    return {"id": job_id, "status": "processing"}


@app.post("/glasses/check")
def glasses_check() -> dict:
    """Start processing glasses recordings that have stopped; Cloud Scheduler calls this every minute."""
    return {"started": check_glasses()}


def current_settings(conn: psycopg.Connection) -> Settings:
    """Return every glasses setting, saved or default."""
    values = {}

    for name, field in Settings.model_fields.items():
        saved = db.setting(conn, name, None)

        if saved is not None:
            values[name] = saved in ("True", "true", "1") if field.annotation is bool else float(saved)

    return Settings(**values)


@app.post("/glasses/frames")
async def glasses_frame(request: Request, x_recording: str = Header(...), x_uploaded: str | None = Header(default=None),
                        x_taken: float | None = Header(default=None),
                        conn: psycopg.Connection = Depends(connection)) -> dict:
    """Take one frame from the glasses, sent here or put in R2 first, and reply with the settings and the next link."""
    if not RECORDING_ID.match(x_recording):
        raise HTTPException(422, "X-Recording must be letters, digits and dashes")

    if x_uploaded is not None:
        if not glasses.UPLOAD_NAME.match(x_uploaded):
            raise HTTPException(422, "X-Uploaded must name an upload link this server gave out")

        path = glasses.arrived(x_recording, x_uploaded, taken=x_taken)

        if path is None:
            raise HTTPException(404, "that frame isn't in storage, or is over 2 MB")
    else:
        jpeg = await request.body()

        if not jpeg.startswith(b"\xff\xd8") or len(jpeg) > MAX_FRAME_BYTES:
            raise HTTPException(422, "the body must be one JPEG under 2 MB")

        path = glasses.save_frame(x_recording, jpeg, taken=x_taken)

    return {"recording": x_recording, "frame": path.name, **current_settings(conn).model_dump(),
            **glasses.upload_slot(x_recording)}


FIRMWARE_DIR = DATA_DIR / "firmware"


def latest_firmware() -> dict | None:
    """Return the newest published glasses firmware's details, read fresh from R2."""
    try:
        return json.loads(storage.bucket().Object(storage.key(FIRMWARE_DIR / "latest.json")).get()["Body"].read())
    except Exception as error:
        if storage.missing(error):
            return None
        raise


@app.get("/glasses/firmware")
def glasses_firmware() -> dict:
    """Say which glasses firmware is newest, with a short-lived link to download it."""
    found = latest_firmware()

    if found is None:
        raise HTTPException(404, "no firmware published")

    image = FIRMWARE_DIR / pathlib.PurePosixPath(found["key"]).name
    url = storage.download_url(image, "application/octet-stream", seconds=3600)

    return {"version": found["version"], "size": found["size"], "crc32": found["crc32"], "url": url}


@app.get("/glasses/status")
def glasses_status(conn: psycopg.Connection = Depends(connection)) -> dict:
    """Say whether the glasses are sending, how many photos the current recording has, and what is processing."""
    latest = glasses.latest()
    (processing,) = conn.execute("select count(*) from job where status = 'processing'").fetchone()

    return {
        "sending": latest is not None and latest["seconds_since_last"] < SENDING_SECONDS,
        "photos": latest["photos"] if latest else 0,
        "seconds_since_last": latest["seconds_since_last"] if latest else None,
        "processing": processing,
    }


@app.get("/uploads")
def uploads(conn: psycopg.Connection = Depends(connection)) -> list[dict]:
    """List recent processing jobs and how far along each is."""
    return db.jobs(conn)


def video_path(moment_id: str, download: bool = True):
    """Return the path of a moment's original video, or None if it wasn't kept."""
    folder = (MOMENTS_DIR / moment_id).resolve()
    video_file = manifest_of(moment_id).get("video_file")

    if not folder.is_relative_to(MOMENTS_DIR) or not video_file:
        return None

    path = folder / video_file

    if not download:
        return path if storage.exists(path) else None

    return storage.local(path)


def has_video(moment_id: str) -> bool:
    return video_path(moment_id, download=False) is not None


def manifest_of(moment_id: str) -> dict:
    """Return a moment's manifest, or an empty one if it can't be read."""
    path = (MOMENTS_DIR / moment_id / "manifest.json").resolve()

    if not path.is_relative_to(MOMENTS_DIR) or storage.local(path) is None:
        return {}

    return json.loads(path.read_text())


def current_extras(moment_id: str) -> dict:
    """Return the extra files kept with a moment, by name."""
    folder = MOMENTS_DIR / moment_id

    return {name: folder / name for name in manifest_of(moment_id).get("extras", [])
            if storage.local(folder / name) is not None}


def caption_cost(conn: psycopg.Connection) -> float:
    """Return the average cost of describing one frame so far."""
    (average,) = conn.execute("select coalesce(avg(usd), 0.006) from model_call where step = 'caption'").fetchone()

    return float(average)


def run_reprocess(job_id, moment_id: str, frames_per_minute: float) -> None:
    """Rebuild a moment's frames at a new rate from its kept video, then caption only what's new."""
    with db.connect() as conn:
        def step(name: str) -> None:
            db.update_job(conn, job_id, step=name)
            conn.commit()

        try:
            pipeline.process(video_path(moment_id), frames_per_minute, current_extras(moment_id), replace=True,
                             reuse_frames=True, on_step=step)
            db.update_job(conn, job_id, status="ready", step=None)
        except Exception as error:
            conn.rollback()
            db.update_job(conn, job_id, status="failed", error=str(error))


@app.get("/places")
def list_places(lat: float | None = None, lon: float | None = None,
                conn: psycopg.Connection = Depends(connection)) -> list[dict]:
    """List places near a location (or all of them), most recently updated first."""
    return rooms.nearby(conn, lat, lon)


@app.get("/world")
def world(conn: psycopg.Connection = Depends(connection)) -> dict:
    """Return every place and every moment that has a location, to show on a map."""
    covers = db.covers(conn)

    return {
        "places": [place for place in db.places(conn) if place["lat"] is not None],
        "moments": [{**moment, "cover": image_url(covers.get(moment["id"]))} for moment in db.located_moments(conn)],
    }


@app.get("/places/{place_id}")
def place_detail(place_id: int, conn: psycopg.Connection = Depends(connection)) -> dict:
    """Return a place's rooms and the moments recorded there."""
    found = next((place for place in db.places(conn) if place["id"] == place_id), None)

    if found is None:
        raise HTTPException(404, "no such place")

    return {**found, "rooms": db.place_rooms(conn, place_id), "moment_ids": db.place_moments(conn, place_id)}


@app.get("/places/{place_id}/scene")
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


@app.get("/places/{place_id}/map")
def place_map_file(place_id: int, conn: psycopg.Connection = Depends(connection)) -> Response:
    """Return a place's saved ARKit map, so the phone can recognise it."""
    path = db.place_map(conn, place_id)
    file = (DATA_DIR / path).resolve() if path else None

    if file is None:
        raise HTTPException(404, "no map for this place")

    return stored_file(file, "application/octet-stream")


@app.put("/places/{place_id}")
def rename_place(place_id: int, body: NewName, conn: psycopg.Connection = Depends(connection)) -> dict:
    """Name or rename a place."""
    name = body.name.strip() if body.name and body.name.strip() else None

    if not db.name_place(conn, place_id, name, by_user=True):
        raise HTTPException(404, "no such place")

    return {"id": place_id, "name": name}


@app.put("/rooms/{room_id}")
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


@app.get("/moments/{moment_id}/path")
def moment_path(moment_id: str) -> dict:
    """Return where the phone went during a moment: [seconds, x, y, z, yaw] in metres and radians."""
    poses_file = (MOMENTS_DIR / moment_id / "poses.json").resolve()

    if not poses_file.is_relative_to(MOMENTS_DIR) or storage.local(poses_file) is None:
        return {"points": []}

    return {"points": thin(load_poses(poses_file))}


@app.get("/moments/{moment_id}/mesh")
def moment_mesh(moment_id: str) -> Response:
    """Return the mesh the phone's lidar captured during a moment, as OBJ."""
    mesh = (MOMENTS_DIR / moment_id / "mesh.obj").resolve()

    if not mesh.is_relative_to(MOMENTS_DIR):
        raise HTTPException(404, "no mesh for this moment")

    return stored_file(mesh, "model/obj")


def check_higher(moment_id: str, frames_per_minute: float) -> None:
    """Refuse a rate that isn't higher than the moment's current one: frames already paid for are kept."""
    current = manifest_of(moment_id).get("frames_per_minute") or 0

    if not current < frames_per_minute <= MAX_FRAMES_PER_MINUTE:
        raise HTTPException(422, f"choose more than {current:g} and at most {MAX_FRAMES_PER_MINUTE} frames a minute")


@app.get("/moments/{moment_id}/reprocess-estimate")
def reprocess_estimate(moment_id: str, frames_per_minute: float,
                       conn: psycopg.Connection = Depends(connection)) -> dict:
    """Say how many new frames a rate would add and what describing them would cost."""
    path = video_path(moment_id)

    if path is None:
        raise HTTPException(404, "this moment's video wasn't kept, so it can't be reprocessed")

    check_higher(moment_id, frames_per_minute)

    manifest = manifest_of(moment_id)
    slots = math.ceil(manifest.get("duration_seconds", 0) * frames_per_minute / 60)
    kept = min(len(manifest.get("frames", [])), slots)

    return {"frames": slots, "new_frames": slots - kept, "kept_captions": kept,
            "usd": round((slots - kept) * caption_cost(conn), 4)}


@app.post("/moments/{moment_id}/reprocess")
def reprocess(moment_id: str, body: Rate, conn: psycopg.Connection = Depends(connection)) -> dict:
    """Rebuild a moment at a different frames per minute, in the background."""
    if video_path(moment_id) is None:
        raise HTTPException(404, "this moment's video wasn't kept, so it can't be reprocessed")

    check_higher(moment_id, body.frames_per_minute)
    task = {"kind": "reprocess", "moment_id": moment_id, "frames_per_minute": body.frames_per_minute}
    job_id = db.new_job(conn, body.frames_per_minute, task)
    db.update_job(conn, job_id, moment_id=moment_id, step="Waiting")
    conn.commit()

    start_job(job_id)

    return {"id": job_id, "status": "processing"}


@app.get("/moments/{moment_id}/video")
def moment_video(moment_id: str) -> Response:
    """Stream a moment's original video."""
    path = video_path(moment_id, download=False)

    if path is None:
        raise HTTPException(404, "no video for this moment")

    return stored_file(path, video_type(path))


def video_type(path) -> str:
    return "video/quicktime" if path.suffix == ".mov" else "video/mp4"


@app.get("/moments/{moment_id}/video/link")
def moment_video_link(moment_id: str) -> dict:
    """Return a short-lived link that plays a moment's video straight from R2."""
    path = video_path(moment_id, download=False)

    if path is None:
        raise HTTPException(404, "no video for this moment")

    return {"url": storage.download_url(path, video_type(path), seconds=3600)}


@app.get("/images/{path:path}")
def image(path: str) -> Response:
    """Serve one frame's JPEG."""
    file = (MOMENTS_DIR / path).resolve()

    if not file.is_relative_to(MOMENTS_DIR) or file.suffix != ".jpg":
        raise HTTPException(404, "no such image")

    return stored_file(file, "image/jpeg")


HISTORY_TURNS = 6


def turn_json(turn: dict, details: dict) -> dict:
    """Return a stored turn in the shape the app reads."""
    answer = None

    if turn["answer"] is not None:
        answer = {
            "answer": turn["answer"],
            "found": turn["found"],
            "sources": [fact_json(fact, details.get(fact["id"])) for fact in turn["sources"]],
            "model_calls": turn["model_calls"],
            "seconds": turn["seconds"],
            "usd": turn["usd"],
            "highlight": turn["highlight"],
            "highlight_reason": turn["highlight_reason"],
            "layout": turn["layout"],
            "follow_ups": turn["follow_ups"],
            "next_steps": turn["next_steps"],
        }

    return {"id": turn["id"], "asked_at": turn["asked_at"], "question": turn["question"], "answer": answer,
            "problem": turn["problem"]}


def turns_json(conn: psycopg.Connection, turns: list[dict]) -> list[dict]:
    """Return turns in the shape the app reads, with who and where for every source."""
    details = db.source_details(conn, [fact for turn in turns for fact in turn["sources"]])

    return [turn_json(turn, details) for turn in turns]


@app.get("/conversations")
def conversations(q: str | None = None, conn: psycopg.Connection = Depends(connection)) -> list[dict]:
    """List conversations, newest first; only those whose title or messages mention `q` if given."""
    return db.conversations(conn, q.strip() if q and q.strip() else None)


@app.post("/conversations")
def start_conversation(conn: psycopg.Connection = Depends(connection)) -> dict:
    """Start an empty conversation."""
    return {"id": db.new_conversation(conn), "turns": []}


@app.get("/conversations/{conversation_id}")
def conversation(conversation_id: UUID, conn: psycopg.Connection = Depends(connection)) -> dict:
    """Return one conversation with every question, answer and source."""
    turns = db.conversation_turns(conn, conversation_id)

    if turns is None:
        raise HTTPException(404, "no such conversation")

    return {"id": conversation_id, "turns": turns_json(conn, turns)}


def prepare(conn: psycopg.Connection, conversation_id: UUID, body: ChatQuestion) -> tuple[list, dict | None, dict]:
    """Return the conversation so far, the frame asked about, and the question as a message to store."""
    turns = db.conversation_turns(conn, conversation_id)

    if turns is None:
        raise HTTPException(404, "no such conversation")

    frame = db.frame_fact(conn, body.frame_id) if body.frame_id else None

    if body.frame_id and frame is None:
        raise HTTPException(404, "no such frame")

    history = [(turn["question"], turn["answer"]) for turn in turns if turn["answer"]][-HISTORY_TURNS:]
    content = {"text": body.question}

    if frame:
        content |= {"frame_id": body.frame_id, "facts": [fact_record(frame)]}

    return history, frame, {"role": "user", "at": datetime.now().astimezone(), "content": content}


def failed(error: Exception) -> list[dict]:
    """Return the message stored when an answer fails."""
    return [{"role": "assistant", "at": datetime.now().astimezone(), "content": {"error": f"The answer failed: {error}"}}]


def saved_turn(conn: psycopg.Connection, conversation_id: UUID, messages: list[dict]) -> dict:
    """Store a question and what followed it, and return that turn as the app reads it."""
    turn_id = db.save_messages(conn, conversation_id, messages)
    turn = next(turn for turn in db.conversation_turns(conn, conversation_id) if turn["id"] == turn_id)

    return turns_json(conn, [turn])[0]


def give_title(conversation_id: UUID, question: str, answer: str | None) -> None:
    """Name a conversation from its first question and answer, if it has no title yet."""
    with db.connect() as conn:
        if db.conversation_title(conn, conversation_id) is None:
            db.set_title(conn, conversation_id, chat.make_title(conn, question, answer or "(no answer)"))


@app.post("/conversations/{conversation_id}/ask")
def ask_in_conversation(conversation_id: UUID, body: ChatQuestion, conn: psycopg.Connection = Depends(connection)) -> dict:
    """Answer a question using the conversation so far, and store both."""
    history, frame, question = prepare(conn, conversation_id, body)

    try:
        replies = ask_agent(conn, body.question, history=history, frame=frame)["messages"]
    except Exception as error:
        conn.rollback()
        replies = failed(error)

    turn = saved_turn(conn, conversation_id, [question, *replies])
    conn.commit()
    give_title(conversation_id, body.question, (turn["answer"] or {}).get("answer"))

    return turn


def event(name: str, data: dict) -> str:
    """Return one server-sent event."""
    return f"event: {name}\ndata: {json.dumps(data, default=str)}\n\n"


def answer_stream(conversation_id: UUID, body: ChatQuestion, history: list, frame: dict | None, question: dict):
    """Yield each step and piece of the answer as server-sent events, then store the turn and send it."""
    with db.connect() as conn:
        replies = None

        try:
            for kind, payload in answer_events(conn, body.question, history=history, frame=frame):
                if kind == "result":
                    replies = payload["messages"]
                elif kind == "step":
                    images = [image_url(path) for path in payload["image_paths"]]
                    yield event("step", {"kind": payload["kind"], "text": payload["text"], "images": images})
                else:
                    yield event(kind, payload)
        except Exception as error:
            conn.rollback()
            replies = failed(error)
            yield event("error", {"message": f"The answer failed: {error}"})

        turn = saved_turn(conn, conversation_id, [question, *replies])
        conn.commit()

        yield event("done", jsonable_encoder(turn))

    give_title(conversation_id, body.question, (turn["answer"] or {}).get("answer"))


@app.post("/conversations/{conversation_id}/ask/stream")
def ask_stream(conversation_id: UUID, body: ChatQuestion,
               conn: psycopg.Connection = Depends(connection)) -> StreamingResponse:
    """Answer a question as server-sent events: each step, the answer as it is written, then the stored turn."""
    history, frame, question = prepare(conn, conversation_id, body)

    return StreamingResponse(answer_stream(conversation_id, body, history, frame, question),
                             media_type="text/event-stream", headers={"Cache-Control": "no-cache"})


SUGGESTIONS: dict = {"moment": None, "questions": []}


@app.get("/suggestions")
def suggestions(conn: psycopg.Connection = Depends(connection)) -> dict:
    """Return questions to suggest, written from the newest records and kept until a new moment arrives."""
    latest = db.latest_moment_id(conn)

    if latest != SUGGESTIONS["moment"] or not SUGGESTIONS["questions"]:
        SUGGESTIONS.update(moment=latest, questions=chat.suggest(conn))

    return {"questions": SUGGESTIONS["questions"]}


@app.delete("/conversations/{conversation_id}")
def delete_conversation(conversation_id: UUID, conn: psycopg.Connection = Depends(connection)) -> dict:
    """Delete a conversation."""
    if not db.delete_conversation(conn, conversation_id):
        raise HTTPException(404, "no such conversation")

    return {"deleted": str(conversation_id)}


FACE_MARGIN = 0.35


def start_rebuild(conn: psycopg.Connection, moment_ids: list[str]) -> None:
    """Rebuild, in the background, the facts of moments whose people or rooms changed."""
    if not moment_ids:
        return

    job_id = db.new_job(conn, 0, {"kind": "facts", "moment_ids": list(moment_ids)})
    conn.commit()
    start_job(job_id)


def rebuild_facts(job_id, moment_ids: list[str]) -> None:
    """Rebuild the facts of moments after a person's or room's name changed."""
    with db.connect() as conn:
        try:
            for moment_id in moment_ids:
                build_facts(conn, moment_id)

            db.update_job(conn, job_id, status="ready")
        except Exception as error:
            conn.rollback()
            db.update_job(conn, job_id, status="failed", error=str(error))


def person_json(person: dict) -> dict:
    """Return a person in the shape the app reads."""
    return {**person, "face": f"/faces/{person['face_id']}" if person["face_id"] else None}


@app.get("/people")
def people(conn: psycopg.Connection = Depends(connection)) -> list[dict]:
    """List everyone whose face was found, most seen first."""
    return [person_json(person) for person in db.people(conn)]


@app.put("/people/{person_id}")
def name_person(person_id: int, body: PersonName,
                conn: psycopg.Connection = Depends(connection)) -> dict:
    """Name or rename a person, then rebuild the facts they appear in."""
    name = body.name.strip() if body.name and body.name.strip() else None

    if not db.name_person(conn, person_id, name):
        raise HTTPException(404, "no such person")

    conn.commit()
    start_rebuild(conn, db.person_moments(conn, person_id))

    return {"id": person_id, "name": name}


@app.post("/people/{person_id}/merge")
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


@app.delete("/people/{person_id}")
def forget_person(person_id: int, conn: psycopg.Connection = Depends(connection)) -> dict:
    """Delete a person and every face of them."""
    moments = db.person_moments(conn, person_id)

    if not db.forget_person(conn, person_id):
        raise HTTPException(404, "no such person")

    conn.commit()
    start_rebuild(conn, moments)

    return {"forgotten": person_id}


@app.get("/faces/{face_id}")
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


@app.get("/stats")
def stats(conn: psycopg.Connection = Depends(connection)) -> dict:
    """Return how much is stored, what it has cost, and the latest eval scores."""
    costs = db.costs(conn)

    return {
        "counts": db.counts(conn),
        "costs": costs,
        "total_usd": sum(cost["usd"] for cost in costs),
        "eval": latest_eval(),
    }


@app.get("/settings")
def get_settings(x_recording: str | None = Header(default=None), conn: psycopg.Connection = Depends(connection)) -> dict:
    """Return the glasses settings, with a link for the first frame when the glasses say which recording is next."""
    slot = glasses.upload_slot(x_recording) if x_recording and RECORDING_ID.match(x_recording) else {}

    return {**current_settings(conn).model_dump(), **slot}


@app.put("/settings")
def put_settings(body: SettingsChange, conn: psycopg.Connection = Depends(connection)) -> Settings:
    """Change the glasses settings that the request includes."""
    for name, value in body.model_dump(exclude_none=True).items():
        db.save_setting(conn, name, value)

    return current_settings(conn)
