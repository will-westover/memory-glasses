"""The database: connect, set up the tables, and store and load what each pipeline step produces.

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


# --- Sessions and frames (recording step) ---

def save_session(conn: psycopg.Connection, session_dir: Path) -> int:
    """Store a session folder's manifest. Saving the same session again replaces it. Return the frame count."""
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
    """Return (id, source, started_at, duration_seconds, frame count, has sound) for each session, newest first."""
    return conn.execute(
        "select s.id, s.source, s.started_at, s.duration_seconds, count(f.id), s.audio_path is not null"
        " from session s left join frame f on f.session_id = s.id"
        " group by s.id order by s.started_at desc"
    ).fetchall()


def session_info(conn: psycopg.Connection, session_id: str) -> tuple:
    """Return (started_at, lat, lon) for a session. lat and lon are None when the recording had no location."""
    return conn.execute("select started_at, lat, lon from session where id = %s", (session_id,)).fetchone()


# --- Captions ---

def frames_without_caption(conn: psycopg.Connection, session_id: str, limit: int | None = None) -> list[tuple]:
    """Return (frame id, image path) for the session's frames that have no caption, oldest first."""
    return conn.execute(
        "select id, image_path from frame where session_id = %s and captioned_at is null"
        " order by captured_at limit %s",
        (session_id, limit),
    ).fetchall()


def save_caption(conn: psycopg.Connection, frame_id: int, caption) -> None:
    """Store a frame's caption, objects and readable text, replacing any earlier ones."""
    conn.execute(
        "update frame set summary = %s, setting = %s, captioned_at = now() where id = %s",
        (caption.summary, caption.setting, frame_id),
    )

    conn.execute("delete from frame_object where frame_id = %s", (frame_id,))
    conn.execute("delete from frame_text where frame_id = %s", (frame_id,))

    with conn.cursor() as cursor:
        cursor.executemany(
            "insert into frame_object (frame_id, name, relation, anchor) values (%s, %s, %s, %s)",
            [(frame_id, item.name, item.relation, item.anchor) for item in caption.objects],
        )

        cursor.executemany(
            "insert into frame_text (frame_id, text, surface) values (%s, %s, %s)",
            [(frame_id, item.text, item.surface) for item in caption.texts],
        )


def captioned_frames(conn: psycopg.Connection, session_id: str) -> list[dict]:
    """Return every captioned frame of a session, oldest first, with its objects and readable text."""
    rows = conn.execute(
        "select f.id, f.image_path, f.captured_at, extract(epoch from f.captured_at - s.started_at),"
        "       f.summary, f.setting,"
        "       (select coalesce(json_agg(json_build_array(o.name, o.relation, o.anchor)), '[]')"
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
            "setting": setting,
            "objects": [tuple(item) for item in objects],
            "texts": [tuple(item) for item in texts],
        }
        for frame_id, image_path, captured_at, offset, summary, setting, objects, texts in rows
    ]


# --- Transcript ---

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


def transcript_lines(conn: psycopg.Connection, session_id: str) -> list[dict]:
    """Return the session's transcript lines with start and end offsets, in order."""
    rows = conn.execute(
        "select start_at, offset_seconds, offset_seconds + extract(epoch from end_at - start_at), speaker, text"
        " from audio_segment where session_id = %s order by start_at",
        (session_id,),
    ).fetchall()

    return [
        {"at": start_at, "start": float(start), "end": float(end), "speaker": speaker, "text": text}
        for start_at, start, end, speaker, text in rows
    ]


# --- Places ---

def places(conn: psycopg.Connection) -> list[tuple]:
    """Return (name, lat, lon, radius in metres) for every saved place."""
    return conn.execute("select name, lat, lon, radius_m from place order by name").fetchall()


def save_place(conn: psycopg.Connection, name: str, lat: float, lon: float, radius_m: float) -> None:
    """Add a named place, or move it if the name is already used."""
    conn.execute(
        "insert into place (name, lat, lon, radius_m) values (%s, %s, %s, %s)"
        " on conflict (name) do update set lat = excluded.lat, lon = excluded.lon, radius_m = excluded.radius_m",
        (name, lat, lon, radius_m),
    )


# --- Facts ---

def replace_facts(conn: psycopg.Connection, session_id: str, facts: list[dict], vectors: list[str]) -> None:
    """Store a session's facts with their embeddings, replacing any earlier ones."""
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


def last_seen(conn: psycopg.Connection, name: str, limit: int = 3) -> list[tuple]:
    """Return the most recent sightings of an object whose name contains `name`, newest first.

    Each row is (object, relation, anchor, seen at, setting, session id, offset seconds, image path).
    """
    return conn.execute(
        "select o.name, o.relation, o.anchor, f.captured_at, f.setting, f.session_id,"
        "       extract(epoch from f.captured_at - s.started_at), f.image_path"
        "  from frame_object o"
        "  join frame f on f.id = o.frame_id"
        "  join session s on s.id = f.session_id"
        " where lower(o.name) like '%%' || lower(%s) || '%%'"
        " order by f.captured_at desc limit %s",
        (name, limit),
    ).fetchall()


# --- Cost ---

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
