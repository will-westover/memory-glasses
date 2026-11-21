"""Settings and stats."""

import json

import psycopg
from fastapi import APIRouter, Depends, Header

from memory import db, glasses, storage
from memory.evals import RUNS_DIR
from memory.api.shared import RECORDING_ID, Settings, SettingsChange, connection, current_settings

router = APIRouter()


def latest_eval() -> dict | None:
    """Return the scores of the newest eval run kept in storage, if there is one."""
    runs = sorted(storage.listing(storage.key(RUNS_DIR) + "/"))

    if not runs:
        return None

    newest = storage.local(storage.path_of(runs[-1]))
    run = json.loads(newest.read_text())

    return {"run": newest.stem, "model": run["model"], "judge": run["judge"], "scores": run["scores"]}


@router.get("/stats")
def stats(conn: psycopg.Connection = Depends(connection)) -> dict:
    """Return how much is stored, what it has cost, and the latest eval scores."""
    costs = db.costs(conn)

    return {
        "counts": db.counts(conn),
        "costs": costs,
        "total_usd": sum(cost["usd"] for cost in costs),
        "eval": latest_eval(),
    }


@router.get("/settings")
def get_settings(x_recording: str | None = Header(default=None), conn: psycopg.Connection = Depends(connection)) -> dict:
    """Return the glasses settings, with a link for the first frame when the glasses say which recording is next."""
    slot = glasses.upload_slot(x_recording) if x_recording and RECORDING_ID.match(x_recording) else {}

    return {**current_settings(conn).model_dump(), **slot}


@router.put("/settings")
def put_settings(body: SettingsChange, conn: psycopg.Connection = Depends(connection)) -> Settings:
    """Change the glasses settings that the request includes."""
    for name, value in body.model_dump(exclude_none=True).items():
        db.save_setting(conn, name, value)

    return current_settings(conn)
