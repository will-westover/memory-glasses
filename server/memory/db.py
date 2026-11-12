"""The database: connect, set up the tables, save sessions and list them.

    uv run python -m memory.db list
    uv run python -m memory.db save ../data/sessions/<id>

Start the database first, from server/:  docker compose up -d --wait
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
    """Run any SQL files in migrations/ that haven't run yet, in name order. Return the ones it ran."""
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
    """Store a session folder's manifest. Saving the same session again replaces it. Return the frame count."""
    manifest = json.loads((session_dir / "manifest.json").read_text())
    session_id = manifest["id"]
    folder = f"sessions/{session_id}"

    with conn.transaction():
        conn.execute("delete from session where id = %s", (session_id,))

        conn.execute(
            "insert into session (id, source, started_at, duration_seconds, audio_path)"
            " values (%s, %s, %s, %s, %s)",
            (
                session_id,
                manifest["source"],
                manifest["started_at"],
                manifest["duration_seconds"],
                f"{folder}/{manifest['audio']}" if manifest["audio"] else None,
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
    """Return (id, source, started_at, duration_seconds, frame count, has sound) for each session, newest first."""
    return conn.execute(
        "select s.id, s.source, s.started_at, s.duration_seconds, count(f.id), s.audio_path is not null"
        " from session s left join frame f on f.session_id = s.id"
        " group by s.id order by s.started_at desc"
    ).fetchall()


def frames_without_caption(conn: psycopg.Connection, session_id: str, limit: int | None = None) -> list[tuple]:
    """Return (frame id, image path) for the session's frames that have no caption, oldest first."""
    return conn.execute(
        "select id, image_path from frame where session_id = %s and captioned_at is null"
        " order by captured_at limit %s",
        (session_id, limit),
    ).fetchall()


def save_caption(conn: psycopg.Connection, frame_id: int, caption) -> None:
    """Store a frame's caption and its objects, replacing any earlier ones."""
    conn.execute(
        "update frame set summary = %s, room = %s, text_seen = %s, captioned_at = now() where id = %s",
        (caption.summary, caption.room, caption.text_seen, frame_id),
    )

    conn.execute("delete from frame_object where frame_id = %s", (frame_id,))
    with conn.cursor() as cursor:
        cursor.executemany(
            "insert into frame_object (frame_id, name, location) values (%s, %s, %s)",
            [(frame_id, item.name, item.location) for item in caption.objects],
        )


def captions(conn: psycopg.Connection, session_id: str) -> dict[str, dict]:
    """Return the session's captions, keyed by frame file name (e.g. '000001.jpg')."""
    rows = conn.execute(
        "select f.image_path, f.summary, f.room, f.text_seen,"
        " coalesce(array_agg(o.name || ' (' || o.location || ')') filter (where o.name is not null), '{}')"
        " from frame f left join frame_object o on o.frame_id = f.id"
        " where f.session_id = %s and f.captioned_at is not null group by f.id",
        (session_id,),
    ).fetchall()

    return {
        Path(image_path).name: {"summary": summary, "room": room, "text_seen": text_seen, "objects": objects}
        for image_path, summary, room, text_seen, objects in rows
    }


def save_transcript(conn: psycopg.Connection, session_id: str, segments) -> None:
    """Store a session's transcript, replacing any earlier one. Each segment has speaker, start, end, text."""
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
    """Return the session's transcript as (offset seconds, speaker, text), in order."""
    return conn.execute(
        "select offset_seconds, speaker, text from audio_segment where session_id = %s order by start_at",
        (session_id,),
    ).fetchall()


def frames_without_embedding(conn: psycopg.Connection, session_id: str) -> list[tuple]:
    """Return (frame id, summary, room, text seen, objects) for captioned frames that have no embedding."""
    return conn.execute(
        "select f.id, f.summary, f.room, f.text_seen,"
        " coalesce(array_agg(o.name || ' ' || o.location) filter (where o.name is not null), '{}')"
        " from frame f left join frame_object o on o.frame_id = f.id"
        " where f.session_id = %s and f.captioned_at is not null and f.embedding is null"
        " group by f.id order by f.captured_at",
        (session_id,),
    ).fetchall()


def lines_without_embedding(conn: psycopg.Connection, session_id: str) -> list[tuple]:
    """Return (line id, text) for transcript lines that have no embedding."""
    return conn.execute(
        "select id, text from audio_segment where session_id = %s and embedding is null order by start_at",
        (session_id,),
    ).fetchall()


def save_frame_embeddings(conn: psycopg.Connection, rows: list[tuple]) -> None:
    """Store (frame id, search text, vector) for each frame."""
    with conn.cursor() as cursor:
        cursor.executemany(
            "update frame set search_text = %s, embedding = %s::vector where id = %s",
            [(text, vector, frame_id) for frame_id, text, vector in rows],
        )


def save_line_embeddings(conn: psycopg.Connection, rows: list[tuple]) -> None:
    """Store (line id, vector) for each transcript line."""
    with conn.cursor() as cursor:
        cursor.executemany(
            "update audio_segment set embedding = %s::vector where id = %s",
            [(vector, line_id) for line_id, vector in rows],
        )


def step_cost(conn: psycopg.Connection, step: str) -> float:
    """Return the total dollars logged for one pipeline step."""
    (usd,) = conn.execute("select coalesce(sum(usd), 0) from api_cost where step = %s", (step,)).fetchone()

    return float(usd)


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
            rows = list_sessions(conn)

            print(f"{'session':<21} {'source':<10} {'length':>7} {'frames':>7}  sound")
            for session_id, source, _, duration, frames, has_sound in rows:
                length = f"{int(duration) // 60}:{int(duration) % 60:02d}"
                print(f"{session_id:<21} {source:<10} {length:>7} {frames:>7}  {'yes' if has_sound else 'no'}")

            if not rows:
                print("(no sessions yet)")
        else:
            print(__doc__, file=sys.stderr)
            return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
