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
