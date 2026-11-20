"""Slow work done away from requests: processing uploads and glasses recordings, reprocessing, rebuilding facts."""

import shutil

import psycopg

from memory import db, glasses, pipeline, storage, tasks
from memory.config import DATA_DIR, FRAMES_PER_MINUTE
from memory.ingest.facts import build_facts
from memory.moment_files import current_extras, video_path

UPLOADS_DIR = DATA_DIR / "uploads"


def start(conn: psycopg.Connection, job_id) -> None:
    """Save the job and start its work, marking it failed if the work can't be started."""
    conn.commit()

    try:
        tasks.start(job_id)
    except Exception as error:
        db.update_job(conn, job_id, status="failed", error=f"couldn't start: {error}")
        conn.commit()
        raise


def check_glasses() -> list[str]:
    """Start processing every glasses recording that has stopped and has no job yet, and return their names."""
    started = []

    for folder in glasses.finished():
        with db.connect() as conn:
            rate = float(db.setting(conn, "frames_per_minute", FRAMES_PER_MINUTE))
            job_id = db.new_job(conn, rate, {"kind": "glasses", "recording": folder.name, "frames_per_minute": rate})

            if job_id is not None:
                start(conn, job_id)
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


def upload_task(names: dict, frames_per_minute: float, place_id: int | None, source: str) -> dict:
    """Describe the work for an uploaded recording: its files and how to process it."""
    return {"kind": "upload", "video": names["video"], "extras": [name for field, name in names.items() if field != "video"],
            "frames_per_minute": frames_per_minute, "place_id": place_id, "source": source}


def process_upload(job_id, task: dict) -> None:
    """Process the recording an upload job describes."""
    folder = UPLOADS_DIR / str(job_id)
    extras = {name: folder / name for name in task["extras"]}

    run_upload(job_id, folder / task["video"], extras, task["frames_per_minute"], task["place_id"], task["source"])


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


def start_rebuild(conn: psycopg.Connection, moment_ids: list[str]) -> None:
    """Rebuild, in the background, the facts of moments whose people or rooms changed."""
    if not moment_ids:
        return

    job_id = db.new_job(conn, 0, {"kind": "facts", "moment_ids": list(moment_ids)})
    start(conn, job_id)


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
