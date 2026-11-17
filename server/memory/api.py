"""The HTTP API the iPhone app talks to."""

import json
import os
from collections.abc import Iterator

import psycopg
from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from memory import db
from memory.agent import ask_agent, ask_plain
from memory.config import DATA_DIR, FRAMES_PER_MINUTE
from memory.evals import RUNS_DIR
from memory.ingest.recording import MAX_FRAMES_PER_MINUTE
from memory.places import place_for

SESSIONS_DIR = (DATA_DIR / "sessions").resolve()


class Exchange(BaseModel):
    question: str
    answer: str


class Question(BaseModel):
    question: str = Field(min_length=1)
    plain: bool = False
    history: list[Exchange] = Field(default=[], max_length=10)


class Settings(BaseModel):
    frames_per_minute: float = Field(ge=0, le=MAX_FRAMES_PER_MINUTE)


def check_token(authorization: str | None = Header(default=None)) -> None:
    """Refuse the request unless it carries the token set in API_TOKEN, when one is set."""
    token = os.environ.get("API_TOKEN")

    if token and authorization != f"Bearer {token}":
        raise HTTPException(401, "missing or wrong token")


def connection() -> Iterator[psycopg.Connection]:
    """Open a database connection for one request."""
    with db.connect() as conn:
        yield conn


app = FastAPI(title="Memory Glasses", dependencies=[Depends(check_token)])


def image_url(image_path: str | None) -> str | None:
    """Return the URL the app loads a frame from."""
    return f"/images/{image_path.removeprefix('sessions/')}" if image_path else None


def fact_json(fact: dict) -> dict:
    """Return a fact in the shape the app reads."""
    return {
        "id": fact["id"],
        "kind": fact["kind"],
        "at": fact["at"],
        "session_id": fact["session_id"],
        "offset_seconds": fact["offset_seconds"],
        "text": fact["text"],
        "image": image_url(fact["image_path"]),
    }


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
    }


def latest_eval() -> dict | None:
    """Return the scores of the newest eval run, if there is one."""
    runs = sorted(RUNS_DIR.glob("*.json"))

    if not runs:
        return None

    run = json.loads(runs[-1].read_text())

    return {"run": runs[-1].stem, "model": run["model"], "judge": run["judge"], "scores": run["scores"]}


@app.get("/sessions")
def sessions(conn: psycopg.Connection = Depends(connection)) -> list[dict]:
    """List every session, newest first."""
    places = db.places(conn)
    covers = db.covers(conn)

    return [
        {
            "id": session_id,
            "source": source,
            "started_at": started_at,
            "duration_seconds": duration,
            "frames": frames,
            "has_sound": has_sound,
            "place": place_for(lat, lon, places),
            "cover": image_url(covers.get(session_id)),
        }
        for session_id, source, started_at, duration, frames, has_sound, lat, lon in db.list_sessions(conn)
    ]


@app.get("/sessions/{session_id}")
def session(session_id: str, conn: psycopg.Connection = Depends(connection)) -> dict:
    """Return one session's frames, transcript and summaries for the timeline."""
    if not db.session_info(conn, session_id):
        raise HTTPException(404, "no such session")

    return {
        "id": session_id,
        "frames": [frame_json(frame) for frame in db.captioned_frames(conn, session_id)],
        "transcript": db.transcript_lines(conn, session_id),
        "summaries": [fact_json(fact) for fact in db.summaries(conn, session_id)],
    }


@app.get("/images/{path:path}")
def image(path: str) -> FileResponse:
    """Serve one frame's JPEG."""
    file = (SESSIONS_DIR / path).resolve()

    if not file.is_relative_to(SESSIONS_DIR) or file.suffix != ".jpg" or not file.is_file():
        raise HTTPException(404, "no such image")

    return FileResponse(file, media_type="image/jpeg")


@app.post("/ask")
def ask(body: Question, conn: psycopg.Connection = Depends(connection)) -> dict:
    """Answer a question, with the facts the answer rests on."""
    if body.plain:
        result = ask_plain(conn, body.question)
    else:
        result = ask_agent(conn, body.question, history=[(item.question, item.answer) for item in body.history])

    return {
        "answer": result["answer"],
        "found": result["found"],
        "sources": [fact_json(fact) for fact in result["sources"]],
        "model_calls": result["model_calls"],
        "seconds": result["seconds"],
        "usd": result["usd"],
    }


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
    return Settings(frames_per_minute=float(db.setting(conn, "frames_per_minute", FRAMES_PER_MINUTE)))


@app.put("/settings")
def put_settings(body: Settings, conn: psycopg.Connection = Depends(connection)) -> Settings:
    """Change the glasses settings."""
    db.save_setting(conn, "frames_per_minute", body.frames_per_minute)

    return body
