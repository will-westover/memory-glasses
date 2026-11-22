"""Slow work done away from requests: processing uploads and glasses recordings, reprocessing, rebuilding facts."""

import json
import os
import shutil
import urllib.request

import psycopg

from memory import db, glasses, pipeline, storage
from memory.config import DATA_DIR, FRAMES_PER_MINUTE
from memory.ingest.facts import build_facts
from memory.moment_files import current_extras, video_path

UPLOADS_DIR = DATA_DIR / "uploads"
METADATA_URL = "http://metadata.google.internal/computeMetadata/v1/"


def cloud_metadata(path: str) -> str:
    """Read one value from the Cloud Run metadata server."""
    request = urllib.request.Request(METADATA_URL + path, headers={"Metadata-Flavor": "Google"})

    with urllib.request.urlopen(request, timeout=5) as response:
        return response.read().decode()


def run_cloud_job(job_id: str) -> None:
    """Start one execution of the worker job for this job id, using the service's own Google identity."""
    token = json.loads(cloud_metadata("instance/service-accounts/default/token"))["access_token"]
    project = cloud_metadata("project/project-id")
    region = os.environ["WORKER_REGION"]
    url = f"https://run.googleapis.com/v2/projects/{project}/locations/{region}/jobs/{os.environ['WORKER_JOB']}:run"
    body = json.dumps({"overrides": {"containerOverrides": [{"args": [job_id]}]}}).encode()

    request = urllib.request.Request(
        url, data=body, method="POST", headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    )

    with urllib.request.urlopen(request, timeout=20) as response:
        response.read()


def start_job(conn: psycopg.Connection, job_id) -> None:
    """Save the job and start its work, marking it failed if the work can't be started."""
    conn.commit()

    try:
        run_cloud_job(str(job_id))
    except Exception as error:
        db.update_job(conn, job_id, status="failed", error=f"couldn't start: {error}")
        conn.commit()
        raise


def check_glasses() -> list[str]:
    """Start processing every glasses recording that was ended or has gone quiet, and return their names."""
    started = []

    with db.connect() as conn:
        rate = float(db.setting(conn, "frames_per_minute", FRAMES_PER_MINUTE))

        for name in db.recordings_to_close(conn, glasses.RECORDING_QUIET_SECONDS):
            job_id = db.new_job(conn, rate, {"kind": "glasses", "recording": name, "frames_per_minute": rate})
            db.close_recording(conn, name, job_id)

            if job_id is not None:
                start_job(conn, job_id)
                started.append(name)

        conn.commit()

    return started


def process_glasses(job_id, task: dict) -> None:
    """Turn a stopped glasses recording into a video and process it like any upload."""
    folder = glasses.GLASSES_DIR / task["recording"]

    try:
        with db.connect() as conn:
            started = db.recording_start(conn, task["recording"])

        video = glasses.build_video(folder, started)
    except Exception as error:
        with db.connect() as conn:
            db.update_job(conn, job_id, status="failed", error=str(error))

        return

    process_video(job_id, video, {}, task["frames_per_minute"], None, "glasses")


def upload_task(file_names: dict, frames_per_minute: float, place_id: int | None, source: str) -> dict:
    """Describe the work for an uploaded recording: its files and how to process it."""
    return {
        "kind": "upload",
        "video": file_names["video"],
        "extras": [name for field, name in file_names.items() if field != "video"],
        "frames_per_minute": frames_per_minute,
        "place_id": place_id,
        "source": source,
    }


def process_upload(job_id, task: dict) -> None:
    """Process the recording an upload job describes."""
    folder = UPLOADS_DIR / str(job_id)
    extras = {name: folder / name for name in task["extras"]}

    process_video(job_id, folder / task["video"], extras, task["frames_per_minute"], task["place_id"], task["source"])


def process_video(job_id, video, extras: dict, frames_per_minute: float, place_id: int | None, source: str) -> None:
    """Process an uploaded recording, keeping its job's status up to date."""
    with db.connect() as conn:

        def report_step(name: str) -> None:
            db.update_job(conn, job_id, step=name)
            conn.commit()

        try:
            storage.fetch_folder(video.parent, videos=True)
            moment_id = pipeline.process(
                video, frames_per_minute, extras, place_id=place_id, source=source, on_step=report_step
            )
            db.update_job(conn, job_id, status="ready", step=None, moment_id=moment_id)

            if video.parent.parent == UPLOADS_DIR:
                shutil.rmtree(video.parent, ignore_errors=True)
                storage.delete_folder(video.parent)
        except Exception as error:
            conn.rollback()
            db.update_job(conn, job_id, status="failed", error=str(error))


def reprocess_moment(job_id, moment_id: str, frames_per_minute: float) -> None:
    """Rebuild a moment's frames at a new rate from its kept video, then caption only what's new."""
    with db.connect() as conn:

        def report_step(name: str) -> None:
            db.update_job(conn, job_id, step=name)
            conn.commit()

        try:
            pipeline.process(
                video_path(moment_id),
                frames_per_minute,
                current_extras(moment_id),
                replace=True,
                reuse_frames=True,
                on_step=report_step,
            )
            db.update_job(conn, job_id, status="ready", step=None)
        except Exception as error:
            conn.rollback()
            db.update_job(conn, job_id, status="failed", error=str(error))


def start_rebuild(conn: psycopg.Connection, moment_ids: list[str]) -> None:
    """Rebuild, in the background, the facts of moments whose people or rooms changed."""
    if not moment_ids:
        return

    job_id = db.new_job(conn, 0, {"kind": "facts", "moment_ids": list(moment_ids)})
    start_job(conn, job_id)


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
