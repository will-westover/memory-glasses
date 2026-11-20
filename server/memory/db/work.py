"""Jobs and the user's settings."""

from datetime import datetime

import psycopg
from psycopg.types.json import Jsonb



def setting(conn: psycopg.Connection, name: str, default):
    """Return a saved setting, or the default if it has never been set."""
    row = conn.execute("select value from user_settings where name = %s", (name,)).fetchone()

    return row[0] if row else default


def save_setting(conn: psycopg.Connection, name: str, value) -> None:
    """Save a setting."""
    conn.execute(
        "insert into user_settings (name, value) values (%s, %s) on conflict (name) do update set value = excluded.value",
        (name, str(value)),
    )


def new_job(conn: psycopg.Connection, frames_per_minute: float, task: dict | None = None,
            status: str = "processing"):
    """Record a new processing job and what it should do, and return its id, or None if that recording has one."""
    row = conn.execute(
        "insert into job (frames_per_minute, task, status) values (%s, %s, %s)"
        " on conflict ((task ->> 'recording')) where task ->> 'recording' is not null do nothing returning id",
        (frames_per_minute, Jsonb(task) if task else None, status),
    ).fetchone()

    return row[0] if row else None


def job_task(conn: psycopg.Connection, job_id) -> dict | None:
    """Return what a job should do."""
    row = conn.execute("select task from job where id = %s", (job_id,)).fetchone()

    return row[0] if row else None


def update_job(conn: psycopg.Connection, job_id, **fields) -> None:
    """Change a job's status, step, moment or error."""
    names = ", ".join(f"{name} = %({name})s" for name in fields)
    conn.execute(f"update job set {names} where id = %(id)s", {**fields, "id": job_id})


def jobs(conn: psycopg.Connection, limit: int = 20) -> list[dict]:
    """Return the most recent jobs, newest first."""
    rows = conn.execute(
        "select id, received_at, status, step, moment_id, error from job"
        " where coalesce(task ->> 'kind', '') <> 'facts' order by received_at desc limit %s",
        (limit,),
    )

    return [
        {"id": job_id, "received_at": received_at, "status": status, "step": step, "moment_id": moment_id,
         "error": error}
        for job_id, received_at, status, step, moment_id, error in rows
    ]


def jobs_processing(conn: psycopg.Connection) -> int:
    """Return how many jobs are being worked on."""
    return conn.execute("select count(*) from job where status = 'processing'").fetchone()[0]


def note_frame(conn: psycopg.Connection, base: str, taken: datetime, arrived: datetime) -> tuple[str, datetime]:
    """Count a frame in the open part of a glasses recording, opening a new part if needed; return (name, start)."""
    row = conn.execute(
        "update recording set last_frame_at = %s, photos = photos + 1 where base = %s and job_id is null"
        " returning name, started_at",
        (arrived, base),
    ).fetchone()

    if row is not None:
        return row

    (parts,) = conn.execute("select count(*) from recording where base = %s", (base,)).fetchone()
    name = base if parts == 0 else f"{base}-part{parts + 1}"

    return conn.execute(
        "insert into recording (name, base, part, started_at, last_frame_at, photos) values (%s, %s, %s, %s, %s, 1)"
        " on conflict (name) do update set last_frame_at = excluded.last_frame_at, photos = recording.photos + 1"
        " returning name, started_at",
        (name, base, parts + 1, taken, arrived),
    ).fetchone()


def end_recording(conn: psycopg.Connection, base: str) -> list[str]:
    """Mark a glasses recording's open part as over, and return its name if there was one."""
    rows = conn.execute(
        "update recording set ended_at = now() where base = %s and job_id is null and ended_at is null returning name",
        (base,),
    )

    return [name for (name,) in rows]


def recordings_to_close(conn: psycopg.Connection, idle_seconds: float) -> list[str]:
    """Return the open glasses recordings that were ended, or have had no frame for idle_seconds."""
    rows = conn.execute(
        "select name from recording where job_id is null and photos > 0"
        " and (ended_at is not null or last_frame_at < now() - make_interval(secs => %s)) order by started_at",
        (idle_seconds,),
    )

    return [name for (name,) in rows]


def close_recording(conn: psycopg.Connection, name: str, job_id) -> None:
    """Tie a glasses recording to the job that processes it, so later frames start a new part."""
    conn.execute(
        "update recording set ended_at = coalesce(ended_at, now()),"
        " job_id = coalesce(%s, (select id from job where task ->> 'recording' = %s)) where name = %s",
        (job_id, name, name),
    )


def recording_start(conn: psycopg.Connection, name: str) -> datetime | None:
    """Return when a glasses recording's first frame was taken."""
    row = conn.execute("select started_at from recording where name = %s", (name,)).fetchone()

    return row[0] if row else None


def latest_recording(conn: psycopg.Connection) -> dict | None:
    """Return how many photos the newest open glasses recording has and how long ago the last one arrived."""
    row = conn.execute(
        "select photos, extract(epoch from now() - last_frame_at) from recording"
        " where job_id is null and ended_at is null order by last_frame_at desc limit 1"
    ).fetchone()

    return {"photos": row[0], "seconds_since_last": round(float(row[1]), 1)} if row else None
