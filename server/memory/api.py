"""The HTTP API the iPhone app talks to."""

import io
import json
import os
import re
import shutil
import threading
import time
import wave
from contextlib import asynccontextmanager
from datetime import datetime
from uuid import UUID
from collections.abc import Iterator

import psycopg
import cv2
from fastapi import BackgroundTasks, Depends, FastAPI, File, Form, Header, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel, Field

from memory import db, glasses, pipeline, rooms
from memory.agent import ask_agent
from memory.config import DATA_DIR, FRAMES_PER_MINUTE
from memory.evals import RUNS_DIR
from memory.ingest.facts import build_facts
from memory.ingest.poses import load_poses, thin
from memory.ingest.recording import kept_frames, planned_frames
from memory.ingest.recording import MAX_FRAMES_PER_MINUTE
from memory.places import place_for

MOMENTS_DIR = (DATA_DIR / "moments").resolve()
MAX_CLIP_SECONDS = 120
UPLOADS_DIR = DATA_DIR / "uploads"
EXTRA_FILES = {"poses": "poses.json", "mesh": "mesh.obj", "world_map": "world.map"}
RECORDING_ID = re.compile(r"^[A-Za-z0-9-]{1,40}$")
MAX_FRAME_BYTES = 2_000_000
CHECK_SECONDS = 15
MIN_DISPLAY_SCALE = 0.3
SENDING_SECONDS = 20


class ChatQuestion(BaseModel):
    question: str = Field(min_length=1)


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
    """Refuse the request unless it carries the token set in API_TOKEN, when one is set."""
    token = os.environ.get("API_TOKEN")

    if token and authorization != f"Bearer {token}":
        raise HTTPException(401, "missing or wrong token")


def connection() -> Iterator[psycopg.Connection]:
    """Open a database connection for one request."""
    with db.connect() as conn:
        yield conn


def process_finished_glasses() -> None:
    """Every few seconds, turn glasses recordings that have stopped into videos and process them."""
    while True:
        for folder in glasses.finished():
            with db.connect() as conn:
                rate = float(db.setting(conn, "frames_per_minute", FRAMES_PER_MINUTE))
                job_id = db.new_job(conn, rate)
                conn.commit()

            try:
                video = glasses.build_video(folder)
            except Exception as error:
                with db.connect() as conn:
                    db.update_job(conn, job_id, status="failed", error=str(error))
                (folder / "video.mp4").touch()
                continue

            run_upload(job_id, video, {}, rate, None, "glasses")

        time.sleep(CHECK_SECONDS)


@asynccontextmanager
async def lifespan(app: FastAPI):
    threading.Thread(target=process_finished_glasses, daemon=True).start()
    yield


app = FastAPI(title="Memory Glasses", dependencies=[Depends(check_token)], lifespan=lifespan)


def image_url(image_path: str | None) -> str | None:
    """Return the URL the app loads a frame from."""
    return f"/images/{image_path.removeprefix('moments/')}" if image_path else None


def fact_json(fact: dict) -> dict:
    """Return a fact in the shape the app reads."""
    return {
        "id": fact["id"],
        "kind": fact["kind"],
        "at": fact["at"],
        "moment_id": fact["moment_id"],
        "offset_seconds": fact["offset_seconds"],
        "text": fact["text"],
        "image": image_url(fact["image_path"]),
    }


def position_json(pose) -> dict | None:
    """Return a frame's position as named fields, or None."""
    return dict(zip(("x", "y", "z", "yaw"), pose)) if pose else None


def frame_json(frame: dict) -> dict:
    """Return a captioned frame in the shape the app reads."""
    return {
        "id": frame["id"],
        "at": frame["at"],
        "offset_seconds": frame["offset_seconds"],
        "image": image_url(frame["image_path"]),
        "summary": frame["summary"],
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
        }
        for moment_id, source, started_at, duration, frames, has_sound, lat, lon in db.list_moments(conn)
    ]


@app.get("/moments/{moment_id}")
def moment(moment_id: str, conn: psycopg.Connection = Depends(connection)) -> dict:
    """Return one moment's frames, transcript and summaries for the timeline."""
    if not db.moment_info(conn, moment_id):
        raise HTTPException(404, "no such moment")

    return {
        "id": moment_id,
        "frames": [frame_json(frame) for frame in db.captioned_frames(conn, moment_id)],
        "transcript": db.transcript_lines(conn, moment_id),
        "summaries": [fact_json(fact) for fact in db.summaries(conn, moment_id)],
        "has_video": has_video(moment_id),
        "frames_per_minute": manifest_of(moment_id).get("frames_per_minute"),
        "has_mesh": (MOMENTS_DIR / moment_id / "mesh.obj").is_file(),
        "space": db.moment_place(conn, moment_id),
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

    if not file.is_relative_to(MOMENTS_DIR) or not file.is_file():
        raise HTTPException(404, "no sound for this moment")

    return Response(audio_clip(file, start, end), media_type="audio/wav")


def save_upload(upload: UploadFile, target) -> None:
    """Write an uploaded file to disk without holding it all in memory."""
    with open(target, "wb") as out:
        shutil.copyfileobj(upload.file, out, length=1024 * 1024)


def run_upload(job_id, video, extras: dict, frames_per_minute: float, place_id: int | None, source: str) -> None:
    """Process an uploaded recording, keeping its job's status up to date."""
    with db.connect() as conn:
        def step(name: str) -> None:
            db.update_job(conn, job_id, step=name)
            conn.commit()

        try:
            moment_id = pipeline.process(video, frames_per_minute, extras, place_id=place_id, source=source,
                                         on_step=step)
            db.update_job(conn, job_id, status="ready", step=None, moment_id=moment_id)
            shutil.rmtree(video.parent, ignore_errors=True)
        except Exception as error:
            conn.rollback()
            db.update_job(conn, job_id, status="failed", error=str(error))


@app.post("/moments")
def upload_moment(
    tasks: BackgroundTasks,
    video: UploadFile = File(...),
    frames_per_minute: float = Form(FRAMES_PER_MINUTE, ge=0, le=MAX_FRAMES_PER_MINUTE),
    poses: UploadFile | None = File(None),
    mesh: UploadFile | None = File(None),
    world_map: UploadFile | None = File(None),
    space_id: int | None = Form(None),
    source: str = Form("iphone", pattern="^(iphone|glasses|video)$"),
    conn: psycopg.Connection = Depends(connection),
) -> dict:
    """Receive a recording and its extra files, and process it in the background."""
    job_id = db.new_job(conn, frames_per_minute)
    conn.commit()

    folder = UPLOADS_DIR / str(job_id)
    folder.mkdir(parents=True)

    suffix = os.path.splitext(video.filename or "")[1].lower() or ".mov"
    video_path = folder / f"video{suffix}"
    save_upload(video, video_path)

    extras = {}
    for field, upload in (("poses", poses), ("mesh", mesh), ("world_map", world_map)):
        if upload is not None:
            extras[EXTRA_FILES[field]] = folder / EXTRA_FILES[field]
            save_upload(upload, extras[EXTRA_FILES[field]])

    tasks.add_task(run_upload, job_id, video_path, extras, frames_per_minute, space_id, source)

    return {"id": job_id, "status": "processing"}


def current_settings(conn: psycopg.Connection) -> Settings:
    """Return every glasses setting, saved or default."""
    values = {}

    for name, field in Settings.model_fields.items():
        saved = db.setting(conn, name, None)

        if saved is not None:
            values[name] = saved in ("True", "true", "1") if field.annotation is bool else float(saved)

    return Settings(**values)


@app.post("/glasses/frames")
async def glasses_frame(request: Request, x_recording: str = Header(...),
                        conn: psycopg.Connection = Depends(connection)) -> dict:
    """Receive one JPEG frame from the glasses and reply with the current settings."""
    if not RECORDING_ID.match(x_recording):
        raise HTTPException(422, "X-Recording must be letters, digits and dashes")

    jpeg = await request.body()

    if not jpeg.startswith(b"\xff\xd8") or len(jpeg) > MAX_FRAME_BYTES:
        raise HTTPException(422, "the body must be one JPEG under 2 MB")

    path = glasses.save_frame(x_recording, jpeg)

    return {"recording": x_recording, "frame": path.name, **current_settings(conn).model_dump()}


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


def video_path(moment_id: str):
    """Return the path of a moment's original video, or None if it wasn't kept."""
    folder = (MOMENTS_DIR / moment_id).resolve()
    manifest = folder / "manifest.json"

    if not folder.is_relative_to(MOMENTS_DIR) or not manifest.is_file():
        return None

    video_file = json.loads(manifest.read_text()).get("video_file")

    return folder / video_file if video_file and (folder / video_file).is_file() else None


def has_video(moment_id: str) -> bool:
    return video_path(moment_id) is not None


def manifest_of(moment_id: str) -> dict:
    """Return a moment's manifest, or an empty one if it can't be read."""
    path = (MOMENTS_DIR / moment_id / "manifest.json").resolve()

    return json.loads(path.read_text()) if path.is_relative_to(MOMENTS_DIR) and path.is_file() else {}


def current_extras(moment_id: str) -> dict:
    """Return the extra files kept with a moment, by name."""
    folder = MOMENTS_DIR / moment_id

    return {name: folder / name for name in manifest_of(moment_id).get("extras", []) if (folder / name).is_file()}


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


@app.get("/spaces")
def list_spaces(lat: float | None = None, lon: float | None = None,
                conn: psycopg.Connection = Depends(connection)) -> list[dict]:
    """List places near a location (or all of them), most recently updated first."""
    return rooms.nearby(conn, lat, lon)


@app.get("/world")
def world(conn: psycopg.Connection = Depends(connection)) -> dict:
    """Return every place and every moment that has a location, to show on a map."""
    covers = db.covers(conn)

    return {
        "spaces": [place for place in db.places(conn) if place["lat"] is not None],
        "moments": [{**moment, "cover": image_url(covers.get(moment["id"]))} for moment in db.located_moments(conn)],
    }


@app.get("/spaces/{space_id}")
def space(space_id: int, conn: psycopg.Connection = Depends(connection)) -> dict:
    """Return a place's rooms and the moments recorded there."""
    found = next((place for place in db.places(conn) if place["id"] == space_id), None)

    if found is None:
        raise HTTPException(404, "no such place")

    return {**found, "rooms": db.place_rooms(conn, space_id), "moment_ids": db.place_moments(conn, space_id)}


@app.get("/spaces/{space_id}/scene")
def space_scene(space_id: int, conn: psycopg.Connection = Depends(connection)) -> dict:
    """Return every moment that shares a place's map, with its path and frames, in the map's coordinates."""
    moments = []
    mesh_moment = None

    for moment_id in db.place_moments(conn, space_id):
        poses_file = MOMENTS_DIR / moment_id / "poses.json"

        if not (db.moment_place(conn, moment_id) or {}).get("in_map"):
            continue

        started_at, _, _ = db.moment_info(conn, moment_id)
        moments.append({
            "id": moment_id,
            "started_at": started_at,
            "path": thin(load_poses(poses_file), 300) if poses_file.is_file() else [],
            "frames": [frame_json(frame) for frame in db.captioned_frames(conn, moment_id)],
        })

        if mesh_moment is None and (MOMENTS_DIR / moment_id / "mesh.obj").is_file():
            mesh_moment = moment_id

    return {"id": space_id, "mesh_moment": mesh_moment, "moments": moments}


@app.get("/spaces/{space_id}/map")
def space_map(space_id: int, conn: psycopg.Connection = Depends(connection)) -> FileResponse:
    """Return a place's saved ARKit map, so the phone can recognise it."""
    path = db.place_map(conn, space_id)
    file = (DATA_DIR / path).resolve() if path else None

    if file is None or not file.is_file():
        raise HTTPException(404, "no map for this place")

    return FileResponse(file, media_type="application/octet-stream")


@app.put("/spaces/{space_id}")
def rename_space(space_id: int, body: NewName, conn: psycopg.Connection = Depends(connection)) -> dict:
    """Name or rename a place."""
    name = body.name.strip() if body.name and body.name.strip() else None

    if not db.name_place(conn, space_id, name, by_user=True):
        raise HTTPException(404, "no such place")

    return {"id": space_id, "name": name}


@app.put("/rooms/{room_id}")
def rename_room(room_id: int, body: NewName, tasks: BackgroundTasks,
                conn: psycopg.Connection = Depends(connection)) -> dict:
    """Rename a room, and rebuild the facts that mention it."""
    name = body.name.strip() if body.name and body.name.strip() else None

    if name is None:
        raise HTTPException(422, "a room needs a name")

    place_id = db.name_room(conn, room_id, name)

    if place_id is None:
        raise HTTPException(404, "no such room")

    conn.commit()
    tasks.add_task(rebuild_facts, db.place_moments(conn, place_id))

    return {"id": room_id, "name": name}


@app.get("/moments/{moment_id}/path")
def moment_path(moment_id: str) -> dict:
    """Return where the phone went during a moment: [seconds, x, y, z, yaw] in metres and radians."""
    poses_file = (MOMENTS_DIR / moment_id / "poses.json").resolve()

    if not poses_file.is_relative_to(MOMENTS_DIR) or not poses_file.is_file():
        return {"points": []}

    return {"points": thin(load_poses(poses_file))}


@app.get("/moments/{moment_id}/mesh")
def moment_mesh(moment_id: str) -> FileResponse:
    """Return the mesh the phone's lidar captured during a moment, as OBJ."""
    mesh = (MOMENTS_DIR / moment_id / "mesh.obj").resolve()

    if not mesh.is_relative_to(MOMENTS_DIR) or not mesh.is_file():
        raise HTTPException(404, "no mesh for this moment")

    return FileResponse(mesh, media_type="model/obj")


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

    planned = planned_frames(path, frames_per_minute, kept_frames(MOMENTS_DIR / moment_id))
    new = sum(1 for frame in planned if not frame["reused"])

    return {"frames": len(planned), "new_frames": new, "kept_captions": len(planned) - new,
            "usd": round(new * caption_cost(conn), 4)}


@app.post("/moments/{moment_id}/reprocess")
def reprocess(moment_id: str, body: Rate, tasks: BackgroundTasks,
              conn: psycopg.Connection = Depends(connection)) -> dict:
    """Rebuild a moment at a different frames per minute, in the background."""
    if video_path(moment_id) is None:
        raise HTTPException(404, "this moment's video wasn't kept, so it can't be reprocessed")

    check_higher(moment_id, body.frames_per_minute)
    job_id = db.new_job(conn, body.frames_per_minute)
    db.update_job(conn, job_id, moment_id=moment_id, step="Waiting")
    conn.commit()

    tasks.add_task(run_reprocess, job_id, moment_id, body.frames_per_minute)

    return {"id": job_id, "status": "processing"}


@app.get("/moments/{moment_id}/video")
def moment_video(moment_id: str) -> FileResponse:
    """Stream a moment's original video."""
    path = video_path(moment_id)

    if path is None:
        raise HTTPException(404, "no video for this moment")

    return FileResponse(path, media_type="video/quicktime" if path.suffix == ".mov" else None)


@app.get("/images/{path:path}")
def image(path: str) -> FileResponse:
    """Serve one frame's JPEG."""
    file = (MOMENTS_DIR / path).resolve()

    if not file.is_relative_to(MOMENTS_DIR) or file.suffix != ".jpg" or not file.is_file():
        raise HTTPException(404, "no such image")

    return FileResponse(file, media_type="image/jpeg")


HISTORY_TURNS = 6


def turn_json(turn: dict) -> dict:
    """Return a stored turn in the shape the app reads."""
    answer = None

    if turn["answer"] is not None:
        answer = {
            "answer": turn["answer"],
            "found": turn["found"],
            "sources": [fact_json(fact) for fact in turn["sources"]],
            "model_calls": turn["model_calls"],
            "seconds": turn["seconds"],
            "usd": turn["usd"],
        }

    return {"id": turn["id"], "asked_at": turn["asked_at"], "question": turn["question"], "answer": answer,
            "problem": turn["problem"]}


@app.get("/conversations")
def conversations(conn: psycopg.Connection = Depends(connection)) -> list[dict]:
    """List conversations, newest first."""
    return db.conversations(conn)


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

    return {"id": conversation_id, "turns": [turn_json(turn) for turn in turns]}


@app.post("/conversations/{conversation_id}/ask")
def ask_in_conversation(conversation_id: UUID, body: ChatQuestion,
                        conn: psycopg.Connection = Depends(connection)) -> dict:
    """Answer a question using the conversation so far, and store both."""
    turns = db.conversation_turns(conn, conversation_id)

    if turns is None:
        raise HTTPException(404, "no such conversation")

    history = [(turn["question"], turn["answer"]) for turn in turns if turn["answer"]][-HISTORY_TURNS:]
    question = {"role": "user", "at": datetime.now().astimezone(), "content": {"text": body.question}}

    try:
        replies = ask_agent(conn, body.question, history=history)["messages"]
    except Exception as error:
        conn.rollback()
        replies = [{"role": "assistant", "at": datetime.now().astimezone(),
                    "content": {"error": f"The answer failed: {error}"}}]

    turn_id = db.save_messages(conn, conversation_id, [question, *replies])

    return turn_json(next(turn for turn in db.conversation_turns(conn, conversation_id) if turn["id"] == turn_id))


@app.delete("/conversations/{conversation_id}")
def delete_conversation(conversation_id: UUID, conn: psycopg.Connection = Depends(connection)) -> dict:
    """Delete a conversation."""
    if not db.delete_conversation(conn, conversation_id):
        raise HTTPException(404, "no such conversation")

    return {"deleted": str(conversation_id)}


FACE_MARGIN = 0.35


def rebuild_facts(moment_ids: list[str]) -> None:
    """Rebuild the facts of moments after a person's or room's name changed."""
    with db.connect() as conn:
        for moment_id in moment_ids:
            build_facts(conn, moment_id)


def person_json(person: dict) -> dict:
    """Return a person in the shape the app reads."""
    return {**person, "face": f"/faces/{person['face_id']}" if person["face_id"] else None}


@app.get("/people")
def people(conn: psycopg.Connection = Depends(connection)) -> list[dict]:
    """List everyone whose face was found, most seen first."""
    return [person_json(person) for person in db.people(conn)]


@app.put("/people/{person_id}")
def name_person(person_id: int, body: PersonName, tasks: BackgroundTasks,
                conn: psycopg.Connection = Depends(connection)) -> dict:
    """Name or rename a person, then rebuild the facts they appear in."""
    name = body.name.strip() if body.name and body.name.strip() else None

    if not db.name_person(conn, person_id, name):
        raise HTTPException(404, "no such person")

    conn.commit()
    tasks.add_task(rebuild_facts, db.person_moments(conn, person_id))

    return {"id": person_id, "name": name}


@app.post("/people/{person_id}/merge")
def merge_person(person_id: int, body: Merge, tasks: BackgroundTasks,
                 conn: psycopg.Connection = Depends(connection)) -> dict:
    """Treat two people as the same person."""
    if person_id == body.into or not db.person_moments(conn, body.into):
        raise HTTPException(422, "merge into a different person who exists")

    moments = db.person_moments(conn, person_id)
    db.merge_people(conn, person_id, body.into)
    conn.commit()
    tasks.add_task(rebuild_facts, moments)

    return {"merged": person_id, "into": body.into}


@app.delete("/people/{person_id}")
def forget_person(person_id: int, tasks: BackgroundTasks, conn: psycopg.Connection = Depends(connection)) -> dict:
    """Delete a person and every face of them."""
    moments = db.person_moments(conn, person_id)

    if not db.forget_person(conn, person_id):
        raise HTTPException(404, "no such person")

    conn.commit()
    tasks.add_task(rebuild_facts, moments)

    return {"forgotten": person_id}


@app.get("/faces/{face_id}")
def face(face_id: int, conn: psycopg.Connection = Depends(connection)) -> Response:
    """Return one face, cropped from its photo with a little margin, as JPEG."""
    found = db.face_crop(conn, face_id)
    image = cv2.imread(str(DATA_DIR / found[0])) if found else None

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
def get_settings(conn: psycopg.Connection = Depends(connection)) -> Settings:
    """Return the glasses settings."""
    return current_settings(conn)


@app.put("/settings")
def put_settings(body: SettingsChange, conn: psycopg.Connection = Depends(connection)) -> Settings:
    """Change the glasses settings that the request includes."""
    for name, value in body.model_dump(exclude_none=True).items():
        db.save_setting(conn, name, value)

    return current_settings(conn)
