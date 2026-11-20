"""Jobs and the user's settings."""

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
