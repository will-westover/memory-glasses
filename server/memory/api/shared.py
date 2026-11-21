"""What every part of the API shares: the connection, the token check, request models and JSON shapes."""

import os
import re
from collections.abc import Iterator

import psycopg
from fastapi import Header, HTTPException
from fastapi.responses import RedirectResponse, Response
from pydantic import BaseModel, Field

from memory import db, storage
from memory.config import FRAMES_PER_MINUTE, STRETCH_SECONDS
from memory.ingest.facts import first_sentence
from memory.ingest.recording import MAX_FRAMES_PER_MINUTE


MAX_CLIP_SECONDS = 120


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


def stored_file(file, media_type: str | None) -> Response:
    """Send a kept file as a redirect to a short-lived R2 link."""
    return RedirectResponse(storage.download_url(file, media_type), status_code=307)


class DirectUpload(BaseModel):
    frames_per_minute: float = Field(FRAMES_PER_MINUTE, ge=0, le=MAX_FRAMES_PER_MINUTE)
    place_id: int | None = None
    source: str = Field("iphone", pattern="^(iphone|glasses|video)$")
    video_suffix: str = Field(".mov", pattern=r"^\.[a-z0-9]{1,5}$")
    extras: list[str] = Field(default=[], max_length=3)


def current_settings(conn: psycopg.Connection) -> Settings:
    """Return every glasses setting, saved or default."""
    values = {}

    for name, field in Settings.model_fields.items():
        saved = db.setting(conn, name, None)

        if saved is not None:
            values[name] = saved in ("True", "true", "1") if field.annotation is bool else float(saved)

    return Settings(**values)


def video_type(path) -> str:
    return "video/quicktime" if path.suffix == ".mov" else "video/mp4"
