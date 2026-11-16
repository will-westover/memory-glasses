"""The database. Start it from server/ with: docker compose up -d --wait

    uv run python -m memory.db list
    uv run python -m memory.db save ../data/sessions/<id>
"""

import json
import sys
from pathlib import Path

import psycopg

from memory.config import DATABASE_URL, SERVER_DIR

MIGRATIONS_DIR = SERVER_DIR / "migrations"


def connect() -> psycopg.Connection:
    """Open a connection and make sure the tables are up to date."""
    conn = psycopg.connect(DATABASE_URL, connect_timeout=3)
    migrate(conn)

    return conn


def migrate(conn: psycopg.Connection) -> list[str]:
    """Run the SQL files in migrations/ that haven't run yet."""
    conn.execute(
        "create table if not exists schema_migration"
        " (name text primary key, applied_at timestamptz not null default now())"
    )

    done = {name for (name,) in conn.execute("select name from schema_migration")}
    ran = []

    for path in sorted(MIGRATIONS_DIR.glob("*.sql")):
        if path.name not in done:
            conn.execute(path.read_text())
            conn.execute("insert into schema_migration (name) values (%s)", (path.name,))
            ran.append(path.name)

    conn.commit()

    return ran


def save_session(conn: psycopg.Connection, session_dir: Path) -> int:
    """Store a session folder's manifest, replacing any earlier copy."""
    manifest = json.loads((session_dir / "manifest.json").read_text())
    session_id = manifest["id"]
    folder = f"sessions/{session_id}"

    with conn.transaction():
        conn.execute("delete from session where id = %s", (session_id,))

        conn.execute(
            "insert into session (id, source, started_at, duration_seconds, audio_path, lat, lon)"
            " values (%s, %s, %s, %s, %s, %s, %s)",
            (
                session_id,
                manifest["source"],
                manifest["started_at"],
                manifest["duration_seconds"],
                f"{folder}/{manifest['audio']}" if manifest["audio"] else None,
                manifest.get("lat"),
                manifest.get("lon"),
            ),
        )

        with conn.cursor() as cursor:
            cursor.executemany(
                "insert into frame (session_id, captured_at, image_path, fingerprint) values (%s, %s, %s, %s)",
                [
                    (session_id, frame["captured_at"], f"{folder}/{frame['file']}", frame["fingerprint"])
                    for frame in manifest["frames"]
                ],
            )

    return len(manifest["frames"])


def list_sessions(conn: psycopg.Connection) -> list[tuple]:
    """Return every session, newest first."""
    return conn.execute(
        "select s.id, s.source, s.started_at, s.duration_seconds, count(f.id), s.audio_path is not null"
        " from session s left join frame f on f.session_id = s.id"
        " group by s.id order by s.started_at desc"
    ).fetchall()


def session_info(conn: psycopg.Connection, session_id: str) -> tuple:
    """Return (started_at, lat, lon) for a session."""
    return conn.execute("select started_at, lat, lon from session where id = %s", (session_id,)).fetchone()


def frames_without_caption(conn: psycopg.Connection, session_id: str, limit: int | None = None) -> list[tuple]:
    """Return (frame id, image path) for frames with no caption."""
    return conn.execute(
        "select id, image_path from frame where session_id = %s and captioned_at is null"
        " order by captured_at limit %s",
        (session_id, limit),
    ).fetchall()


def save_caption(conn: psycopg.Connection, frame_id: int, caption) -> None:
    """Store a frame's caption, replacing any earlier one."""
    conn.execute(
        "update frame set summary = %s, captioned_at = now() where id = %s",
        (caption.summary, frame_id),
    )

    conn.execute("delete from frame_object where frame_id = %s", (frame_id,))
    conn.execute("delete from frame_text where frame_id = %s", (frame_id,))

    with conn.cursor() as cursor:
        cursor.executemany(
            "insert into frame_object (frame_id, name, location) values (%s, %s, %s)",
            [(frame_id, item.name, item.location) for item in caption.objects],
        )

        cursor.executemany(
            "insert into frame_text (frame_id, text, surface) values (%s, %s, %s)",
            [(frame_id, item.text, item.surface) for item in caption.texts],
        )


def clear_captions(conn: psycopg.Connection, session_id: str) -> None:
    """Mark every frame of a session as not captioned."""
    conn.execute("update frame set captioned_at = null where session_id = %s", (session_id,))


def captioned_frames(conn: psycopg.Connection, session_id: str) -> list[dict]:
    """Return every captioned frame of a session, oldest first."""
    rows = conn.execute(
        "select f.id, f.image_path, f.captured_at, extract(epoch from f.captured_at - s.started_at),"
        "       f.summary,"
        "       (select coalesce(json_agg(json_build_array(o.name, o.location)), '[]')"
        "          from frame_object o where o.frame_id = f.id),"
        "       (select coalesce(json_agg(json_build_array(t.text, t.surface)), '[]')"
        "          from frame_text t where t.frame_id = f.id)"
        "  from frame f join session s on s.id = f.session_id"
        " where f.session_id = %s and f.captioned_at is not null"
        " order by f.captured_at",
        (session_id,),
    ).fetchall()

    return [
        {
            "id": frame_id,
            "image_path": image_path,
            "at": captured_at,
            "offset_seconds": float(offset),
            "summary": summary,
            "objects": [tuple(item) for item in objects],
            "texts": [tuple(item) for item in texts],
        }
        for frame_id, image_path, captured_at, offset, summary, objects, texts in rows
    ]


def save_transcript(conn: psycopg.Connection, session_id: str, segments) -> None:
    """Store a session's transcript, replacing any earlier one."""
    conn.execute("delete from audio_segment where session_id = %s", (session_id,))

    with conn.cursor() as cursor:
        cursor.executemany(
            "insert into audio_segment (session_id, start_at, end_at, offset_seconds, speaker, text)"
            " select id, started_at + make_interval(secs => %s), started_at + make_interval(secs => %s), %s, %s, %s"
            " from session where id = %s",
            [
                (segment.start, segment.end, segment.start, segment.speaker, segment.text.strip(), session_id)
                for segment in segments
                if segment.text.strip()
            ],
        )


def transcript(conn: psycopg.Connection, session_id: str) -> list[tuple]:
    """Return the transcript as (offset seconds, speaker, text)."""
    return conn.execute(
        "select offset_seconds, speaker, text from audio_segment where session_id = %s order by start_at",
        (session_id,),
    ).fetchall()


def transcript_lines(conn: psycopg.Connection, session_id: str) -> list[dict]:
    """Return the transcript lines with start and end offsets."""
    rows = conn.execute(
        "select start_at, offset_seconds, offset_seconds + extract(epoch from end_at - start_at), speaker, text"
        " from audio_segment where session_id = %s order by start_at",
        (session_id,),
    ).fetchall()

    return [
        {"at": start_at, "start": float(start), "end": float(end), "speaker": speaker, "text": text}
        for start_at, start, end, speaker, text in rows
    ]


def places(conn: psycopg.Connection) -> list[tuple]:
    """Return (name, lat, lon, radius_m) for every place."""
    return conn.execute("select name, lat, lon, radius_m from place order by name").fetchall()


def save_place(conn: psycopg.Connection, name: str, lat: float, lon: float, radius_m: float) -> None:
    """Add a named place, or move it if the name is already used."""
    conn.execute(
        "insert into place (name, lat, lon, radius_m) values (%s, %s, %s, %s)"
        " on conflict (name) do update set lat = excluded.lat, lon = excluded.lon, radius_m = excluded.radius_m",
        (name, lat, lon, radius_m),
    )


def replace_facts(conn: psycopg.Connection, session_id: str, facts: list[dict], vectors: list[str]) -> None:
    """Store a session's facts, replacing any earlier ones."""
    conn.execute("delete from fact where session_id = %s", (session_id,))

    with conn.cursor() as cursor:
        cursor.executemany(
            "insert into fact (session_id, frame_id, kind, at, offset_seconds, text, embedding)"
            " values (%s, %s, %s, %s, %s, %s, %s::vector)",
            [
                (session_id, fact["frame_id"], fact["kind"], fact["at"], fact["offset_seconds"], fact["text"], vector)
                for fact, vector in zip(facts, vectors)
            ],
        )


FACT_COLUMNS = (
    "select fact.id, fact.kind, fact.at, fact.session_id, fact.offset_seconds, fact.text, frame.image_path"
    "  from fact left join frame on frame.id = fact.frame_id"
)


def fact_rows(rows) -> list[dict]:
    return [
        {"id": fact_id, "kind": kind, "at": at, "session_id": session_id, "offset_seconds": float(offset),
         "text": text, "image_path": image_path}
        for fact_id, kind, at, session_id, offset, text, image_path in rows
    ]


def facts_by_id(conn: psycopg.Connection, fact_ids: list[int]) -> list[dict]:
    """Return the facts with these ids."""
    return fact_rows(conn.execute(f"{FACT_COLUMNS} where fact.id = any(%s)", (fact_ids,)))


def timeline(conn: psycopg.Connection, start, end) -> list[dict]:
    """Return the moments, speech and summaries between two times, in order."""
    return fact_rows(conn.execute(
        f"{FACT_COLUMNS} where fact.kind in ('moment', 'speech', 'summary') and fact.at between %s and %s"
        " order by fact.at, fact.id",
        (start, end),
    ))


def step_cost(conn: psycopg.Connection, step: str) -> float:
    """Return the total dollars logged for one pipeline step."""
    (usd,) = conn.execute("select coalesce(sum(usd), 0) from api_cost where step = %s", (step,)).fetchone()

    return float(usd)


def print_sessions(rows: list[tuple]) -> None:
    print(f"{'session':<21} {'source':<10} {'length':>7} {'frames':>7}  sound")

    for session_id, source, _, duration, frames, has_sound in rows:
        length = f"{int(duration) // 60}:{int(duration) % 60:02d}"
        print(f"{session_id:<21} {source:<10} {length:>7} {frames:>7}  {'yes' if has_sound else 'no'}")

    if not rows:
        print("(no sessions yet)")


def main() -> int:
    command = sys.argv[1] if len(sys.argv) > 1 else "list"

    try:
        conn = connect()
    except psycopg.OperationalError:
        print("error: can't reach the database. Start it from server/ with: docker compose up -d --wait",
              file=sys.stderr)
        return 1

    with conn:
        if command == "save" and len(sys.argv) == 3:
            count = save_session(conn, Path(sys.argv[2]))
            print(f"Saved {count} frames.")
        elif command == "list":
            print_sessions(list_sessions(conn))
        else:
            print(__doc__, file=sys.stderr)
            return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
