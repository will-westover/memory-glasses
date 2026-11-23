"""Recordings uploaded from the phone straight to storage."""

from uuid import UUID

import psycopg
from fastapi import APIRouter, Depends, HTTPException

from server import db
from server.jobs import jobs
from server.shared import storage
from server.jobs.jobs import UPLOADS_DIR, upload_task
from server.api.shared import DirectUpload, EXTRA_FILES, connection

router = APIRouter()


@router.post("/uploads")
def start_upload(body: DirectUpload, conn: psycopg.Connection = Depends(connection)) -> dict:
    """Start an upload straight to storage: return a link to PUT each file to, valid for an hour."""
    file_names = {"video": f"video{body.video_suffix}"} | {
        field: EXTRA_FILES[field] for field in body.extras if field in EXTRA_FILES
    }
    task = upload_task(file_names, body.frames_per_minute, body.place_id, body.source)
    job_id = db.new_job(conn, body.frames_per_minute, task, status="uploading")
    db.update_job(conn, job_id, step="Uploading")
    conn.commit()

    folder = UPLOADS_DIR / str(job_id)

    return {"id": job_id, "urls": {field: storage.upload_url(folder / name) for field, name in file_names.items()}}


@router.post("/uploads/{job_id}/done")
def finish_upload(job_id: UUID, conn: psycopg.Connection = Depends(connection)) -> dict:
    """Start processing a recording once its files are in storage."""
    task = db.job_task(conn, job_id)

    if task is None or task.get("kind") != "upload":
        raise HTTPException(404, "no such upload")

    if not storage.exists(UPLOADS_DIR / str(job_id) / task["video"]):
        raise HTTPException(409, "the video hasn't arrived yet")

    db.update_job(conn, job_id, status="processing", step=None)
    jobs.start_job(conn, job_id)

    return {"id": job_id, "status": "processing"}


@router.post("/uploads/{job_id}/retry")
def retry_upload(job_id: UUID, conn: psycopg.Connection = Depends(connection)) -> dict:
    """Try a failed job again."""
    if not db.retry_job(conn, job_id):
        raise HTTPException(404, "no failed job with that id")

    jobs.start_job(conn, job_id)

    return {"id": job_id, "status": "processing"}


@router.get("/uploads")
def uploads(conn: psycopg.Connection = Depends(connection)) -> list[dict]:
    """List recent processing jobs and how far along each is."""
    return db.recent_jobs(conn)
