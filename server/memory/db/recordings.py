"""Glasses recordings: the photos arriving for each, and when each ends."""

from datetime import datetime

import psycopg


def note_frame(conn: psycopg.Connection, recording: str, taken: datetime, arrived: datetime) -> tuple[str, datetime]:
    """Count a frame in the open part of a glasses recording, opening a new part if needed; return (name, start)."""
    row = conn.execute(
        "update recording set last_frame_at = %s, photos = photos + 1 where base = %s and job_id is null"
        " returning name, started_at",
        (arrived, recording),
    ).fetchone()

    if row is not None:
        return row

    (part_count,) = conn.execute("select count(*) from recording where base = %s", (recording,)).fetchone()
    name = recording if part_count == 0 else f"{recording}-part{part_count + 1}"

    return conn.execute(
        "insert into recording (name, base, part, started_at, last_frame_at, photos) values (%s, %s, %s, %s, %s, 1)"
        " on conflict (name) do update set last_frame_at = excluded.last_frame_at, photos = recording.photos + 1"
        " returning name, started_at",
        (name, recording, part_count + 1, taken, arrived),
    ).fetchone()


def end_recording(conn: psycopg.Connection, recording: str) -> list[str]:
    """Mark a glasses recording's open part as over, and return its name if there was one."""
    rows = conn.execute(
        "update recording set ended_at = now() where base = %s and job_id is null and ended_at is null returning name",
        (recording,),
    )

    return [name for (name,) in rows]


def ended_recordings(conn: psycopg.Connection) -> list[str]:
    """Return the glasses recordings that were ended and aren't being processed yet."""
    rows = conn.execute(
        "select name from recording where job_id is null and photos > 0 and ended_at is not null order by started_at"
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
