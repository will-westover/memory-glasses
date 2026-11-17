"""The database: tables, and storing and loading what each step produces."""

import json
import sys
from datetime import timedelta
from pathlib import Path

import psycopg

from memory.config import DATABASE_URL, SERVER_DIR, STRETCH_SECONDS, clock

MIGRATIONS_DIR = SERVER_DIR / "migrations"
USAGE = "usage: python -m memory.db list | save <moment folder>"


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


def save_moment(conn: psycopg.Connection, moment_dir: Path) -> dict:
    """Store a moment folder's manifest, keeping the captions of frames that are unchanged."""
    manifest = json.loads((moment_dir / "manifest.json").read_text())
    moment_id = manifest["id"]
    folder = f"moments/{moment_id}"

    wanted = {(f"{folder}/{frame['file']}", frame["fingerprint"]): frame["captured_at"] for frame in manifest["frames"]}

    with conn.transaction():
        conn.execute(
            "insert into moment (id, source, started_at, duration_seconds, audio_path, lat, lon)"
            " values (%(id)s, %(source)s, %(started_at)s, %(duration)s, %(audio)s, %(lat)s, %(lon)s)"
            " on conflict (id) do update set source = excluded.source, started_at = excluded.started_at,"
            " duration_seconds = excluded.duration_seconds, audio_path = excluded.audio_path,"
            " lat = excluded.lat, lon = excluded.lon",
            {
                "id": moment_id,
                "source": manifest["source"],
                "started_at": manifest["started_at"],
                "duration": manifest["duration_seconds"],
                "audio": f"{folder}/{manifest['audio']}" if manifest["audio"] else None,
                "lat": manifest.get("lat"),
                "lon": manifest.get("lon"),
            },
        )

        stored = {
            (image_path, fingerprint): (frame_id, captioned)
            for frame_id, image_path, fingerprint, captioned in conn.execute(
                "select id, image_path, fingerprint, captioned_at is not null from frame where moment_id = %s",
                (moment_id,),
            )
        }

        gone = [stored[key] for key in stored.keys() - wanted.keys()]
        conn.execute("delete from frame where id = any(%s)", ([frame_id for frame_id, _ in gone],))

        with conn.cursor() as cursor:
            cursor.executemany(
                "update frame set captured_at = %s where id = %s",
                [(wanted[key], stored[key][0]) for key in wanted.keys() & stored.keys()],
            )

            cursor.executemany(
                "insert into frame (moment_id, captured_at, image_path, fingerprint) values (%s, %s, %s, %s)",
                [(moment_id, wanted[key], *key) for key in wanted.keys() - stored.keys()],
            )

    return {
        "frames": len(wanted),
        "captions_kept": sum(stored[key][1] for key in wanted.keys() & stored.keys()),
        "captions_lost": sum(captioned for _, captioned in gone),
        "changed": bool(stored) and wanted.keys() != stored.keys(),
    }


def list_moments(conn: psycopg.Connection) -> list[tuple]:
    """Return every moment, newest first."""
    return conn.execute(
        "select s.id, s.source, s.started_at, s.duration_seconds, count(f.id), s.audio_path is not null,"
        "       s.lat, s.lon"
        " from moment s left join frame f on f.moment_id = s.id"
        " group by s.id order by s.started_at desc"
    ).fetchall()


def moment_info(conn: psycopg.Connection, moment_id: str) -> tuple:
    """Return (started_at, lat, lon) for a moment."""
    return conn.execute("select started_at, lat, lon from moment where id = %s", (moment_id,)).fetchone()


def frames_without_caption(conn: psycopg.Connection, moment_id: str, limit: int | None = None) -> list[tuple]:
    """Return (frame id, image path) for frames with no caption."""
    return conn.execute(
        "select id, image_path from frame where moment_id = %s and captioned_at is null"
        " order by captured_at limit %s",
        (moment_id, limit),
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


def clear_captions(conn: psycopg.Connection, moment_id: str) -> None:
    """Mark every frame of a moment as not captioned."""
    conn.execute("update frame set captioned_at = null where moment_id = %s", (moment_id,))


def captioned_frames(conn: psycopg.Connection, moment_id: str) -> list[dict]:
    """Return every captioned frame of a moment, oldest first."""
    rows = conn.execute(
        "select f.id, f.image_path, f.captured_at, extract(epoch from f.captured_at - s.started_at),"
        "       f.summary,"
        "       (select coalesce(json_agg(json_build_array(o.name, o.location)), '[]')"
        "          from frame_object o where o.frame_id = f.id),"
        "       (select coalesce(json_agg(json_build_array(t.text, t.surface)), '[]')"
        "          from frame_text t where t.frame_id = f.id)"
        "       ,(select coalesce(json_agg(distinct jsonb_build_object('id', p.id, 'name', p.name, 'face_id',"
        "          (select f2.id from face f2 where f2.person_id = p.id order by f2.w * f2.h * f2.score desc limit 1))), '[]')"
        "          from face fc join person p on p.id = fc.person_id where fc.frame_id = f.id)"
        "  from frame f join moment s on s.id = f.moment_id"
        " where f.moment_id = %s and f.captioned_at is not null"
        " order by f.captured_at",
        (moment_id,),
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
            "people": people,
        }
        for frame_id, image_path, captured_at, offset, summary, objects, texts, people in rows
    ]


def save_transcript(conn: psycopg.Connection, moment_id: str, segments) -> None:
    """Store a moment's transcript, replacing any earlier one."""
    conn.execute("delete from audio_segment where moment_id = %s", (moment_id,))

    with conn.cursor() as cursor:
        cursor.executemany(
            "insert into audio_segment (moment_id, start_at, end_at, offset_seconds, speaker, text)"
            " select id, started_at + make_interval(secs => %s), started_at + make_interval(secs => %s), %s, %s, %s"
            " from moment where id = %s",
            [
                (segment.start, segment.end, segment.start, segment.speaker, segment.text.strip(), moment_id)
                for segment in segments
                if segment.text.strip()
            ],
        )


def transcript(conn: psycopg.Connection, moment_id: str) -> list[tuple]:
    """Return the transcript as (offset seconds, speaker, text)."""
    return conn.execute(
        "select offset_seconds, speaker, text from audio_segment where moment_id = %s order by start_at",
        (moment_id,),
    ).fetchall()


def transcript_lines(conn: psycopg.Connection, moment_id: str) -> list[dict]:
    """Return the transcript lines with start and end offsets."""
    rows = conn.execute(
        "select start_at, offset_seconds, offset_seconds + extract(epoch from end_at - start_at), speaker, text"
        " from audio_segment where moment_id = %s order by start_at",
        (moment_id,),
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


def replace_facts(conn: psycopg.Connection, moment_id: str, facts: list[dict], vectors: list[str]) -> None:
    """Store a moment's facts, replacing any earlier ones."""
    conn.execute("delete from fact where moment_id = %s", (moment_id,))

    with conn.cursor() as cursor:
        cursor.executemany(
            "insert into fact (moment_id, frame_id, kind, at, offset_seconds, text, embedding)"
            " values (%s, %s, %s, %s, %s, %s, %s::vector)",
            [
                (moment_id, fact["frame_id"], fact["kind"], fact["at"], fact["offset_seconds"], fact["text"], vector)
                for fact, vector in zip(facts, vectors)
            ],
        )


FACT_COLUMNS = (
    "select fact.id, fact.kind, fact.at, fact.moment_id, fact.offset_seconds, fact.text, frame.image_path"
    "  from fact left join frame on frame.id = fact.frame_id"
)


def fact_rows(rows) -> list[dict]:
    return [
        {"id": fact_id, "kind": kind, "at": at, "moment_id": moment_id, "offset_seconds": float(offset),
         "text": text, "image_path": image_path}
        for fact_id, kind, at, moment_id, offset, text, image_path in rows
    ]


def facts_by_id(conn: psycopg.Connection, fact_ids: list[int]) -> list[dict]:
    """Return the facts with these ids."""
    return fact_rows(conn.execute(f"{FACT_COLUMNS} where fact.id = any(%s)", (fact_ids,)))


def timeline(conn: psycopg.Connection, start, end) -> list[dict]:
    """Return the frames, speech and summaries between two times, in order."""
    return fact_rows(conn.execute(
        f"{FACT_COLUMNS} where fact.at <= %(end)s and ("
        "    (fact.kind in ('frame', 'speech') and fact.at >= %(start)s)"
        " or (fact.kind = 'summary' and fact.at > %(summary_start)s))"
        " order by fact.at, fact.id",
        {"start": start, "end": end, "summary_start": start - timedelta(seconds=STRETCH_SECONDS)},
    ))


def step_cost(conn: psycopg.Connection, step: str) -> float:
    """Return the total dollars logged for one pipeline step."""
    (usd,) = conn.execute("select coalesce(sum(usd), 0) from api_cost where step = %s", (step,)).fetchone()

    return float(usd)


def summaries(conn: psycopg.Connection, moment_id: str) -> list[dict]:
    """Return a moment's summary facts, in order."""
    return fact_rows(conn.execute(
        f"{FACT_COLUMNS} where fact.moment_id = %s and fact.kind = 'summary' order by fact.at", (moment_id,)
    ))


def covers(conn: psycopg.Connection) -> dict[str, str]:
    """Return the first frame's image path for each moment."""
    rows = conn.execute("select distinct on (moment_id) moment_id, image_path from frame order by moment_id, captured_at")

    return dict(rows.fetchall())


def costs(conn: psycopg.Connection) -> list[dict]:
    """Return the calls, tokens and dollars logged for each pipeline step."""
    rows = conn.execute(
        "select step, count(*), sum(input_tokens), sum(output_tokens), sum(usd) from api_cost"
        " group by step order by sum(usd) desc"
    )

    return [
        {"step": step, "calls": calls, "input_tokens": tokens_in, "output_tokens": tokens_out, "usd": float(usd)}
        for step, calls, tokens_in, tokens_out, usd in rows
    ]


def counts(conn: psycopg.Connection) -> dict:
    """Return how many moments, frames and facts are stored."""
    moments, frames, facts = conn.execute(
        "select (select count(*) from moment), (select count(*) from frame), (select count(*) from fact)"
    ).fetchone()

    return {"moments": moments, "frames": frames, "facts": facts}


def setting(conn: psycopg.Connection, name: str, default):
    """Return a saved setting, or the default if it has never been set."""
    row = conn.execute("select value from setting where name = %s", (name,)).fetchone()

    return row[0] if row else default


def save_setting(conn: psycopg.Connection, name: str, value) -> None:
    """Save a setting."""
    conn.execute(
        "insert into setting (name, value) values (%s, %s) on conflict (name) do update set value = excluded.value",
        (name, str(value)),
    )


def conversations(conn: psycopg.Connection) -> list[dict]:
    """Return every conversation with its first question and latest answer, newest first."""
    rows = conn.execute(
        "select c.id, c.updated_at,"
        "       (select question from turn where conversation_id = c.id order by asked_at limit 1),"
        "       (select coalesce(answer, problem) from turn where conversation_id = c.id order by asked_at desc limit 1)"
        "  from conversation c"
        " where exists (select 1 from turn where conversation_id = c.id)"
        " order by c.updated_at desc"
    )

    return [
        {"id": conversation_id, "updated_at": updated_at, "title": title, "preview": preview or ""}
        for conversation_id, updated_at, title, preview in rows
    ]


def conversation_turns(conn: psycopg.Connection, conversation_id) -> list[dict] | None:
    """Return a conversation's turns, oldest first, each with its sources; None if there is no such conversation."""
    if not conn.execute("select 1 from conversation where id = %s", (conversation_id,)).fetchone():
        return None

    turns = conn.execute(
        "select id, asked_at, question, answer, found, problem, model_calls, seconds, usd"
        "  from turn where conversation_id = %s order by asked_at",
        (conversation_id,),
    ).fetchall()

    sources = conn.execute(
        "select s.turn_id, s.fact_id, s.kind, s.at, s.moment_id, s.offset_seconds, s.text, s.image_path"
        "  from turn_source s join turn t on t.id = s.turn_id"
        " where t.conversation_id = %s order by s.turn_id, s.position",
        (conversation_id,),
    ).fetchall()

    by_turn: dict = {}
    for turn_id, fact_id, kind, at, moment_id, offset, text, image_path in sources:
        by_turn.setdefault(turn_id, []).append({
            "id": fact_id, "kind": kind, "at": at, "moment_id": moment_id,
            "offset_seconds": float(offset), "text": text, "image_path": image_path,
        })

    return [
        {
            "id": turn_id, "asked_at": asked_at, "question": question, "answer": answer, "found": found,
            "problem": problem, "model_calls": model_calls, "seconds": seconds,
            "usd": float(usd) if usd is not None else None, "sources": by_turn.get(turn_id, []),
        }
        for turn_id, asked_at, question, answer, found, problem, model_calls, seconds, usd in turns
    ]


def new_conversation(conn: psycopg.Connection):
    """Create an empty conversation and return its id."""
    (conversation_id,) = conn.execute("insert into conversation default values returning id").fetchone()

    return conversation_id


def save_turn(conn: psycopg.Connection, conversation_id, question: str, result: dict | None, problem: str | None):
    """Store one question and its answer or problem, with copies of its sources. Return the turn's id."""
    with conn.transaction():
        (turn_id,) = conn.execute(
            "insert into turn (conversation_id, question, answer, found, problem, model_calls, seconds, usd)"
            " values (%s, %s, %s, %s, %s, %s, %s, %s) returning id",
            (
                conversation_id, question,
                result and result["answer"], result and result["found"], problem,
                result and result["model_calls"], result and result["seconds"], result and result["usd"],
            ),
        ).fetchone()

        with conn.cursor() as cursor:
            cursor.executemany(
                "insert into turn_source (turn_id, position, fact_id, kind, at, moment_id, offset_seconds, text,"
                " image_path) values (%s, %s, %s, %s, %s, %s, %s, %s, %s)",
                [
                    (turn_id, position, fact["id"], fact["kind"], fact["at"], fact["moment_id"],
                     fact["offset_seconds"], fact["text"], fact["image_path"])
                    for position, fact in enumerate(result["sources"] if result else [])
                ],
            )

        conn.execute("update conversation set updated_at = now() where id = %s", (conversation_id,))

    return turn_id


def delete_conversation(conn: psycopg.Connection, conversation_id) -> bool:
    """Delete a conversation and everything in it. Return whether it existed."""
    return conn.execute("delete from conversation where id = %s", (conversation_id,)).rowcount > 0


def frame_images(conn: psycopg.Connection, moment_id: str) -> list[tuple]:
    """Return (frame id, image path) for every frame of a moment, oldest first."""
    return conn.execute(
        "select id, image_path from frame where moment_id = %s order by captured_at", (moment_id,)
    ).fetchall()


def clear_faces(conn: psycopg.Connection, moment_id: str) -> None:
    """Delete the faces found in a moment's frames."""
    conn.execute("delete from face where frame_id in (select id from frame where moment_id = %s)", (moment_id,))


def person_centres(conn: psycopg.Connection) -> dict:
    """Return each person's summed face embedding, keyed by person id."""
    import numpy as np

    rows = conn.execute("select person_id, sum(embedding)::text from face group by person_id")

    return {person_id: np.array([float(v) for v in total.strip("[]").split(",")]) for person_id, total in rows}


def new_person(conn: psycopg.Connection) -> int:
    """Create an unnamed person and return their id."""
    (person_id,) = conn.execute("insert into person default values returning id").fetchone()

    return person_id


def save_face(conn: psycopg.Connection, frame_id: int, person_id: int, box, score: float, vector: str) -> None:
    """Store one face found in a frame."""
    conn.execute(
        "insert into face (frame_id, person_id, x, y, w, h, score, embedding) values (%s, %s, %s, %s, %s, %s, %s, %s)",
        (frame_id, person_id, *box, score, vector),
    )


def remove_faceless_people(conn: psycopg.Connection) -> None:
    """Delete unnamed people who no longer have any faces."""
    conn.execute("delete from person p where name is null and not exists (select 1 from face where person_id = p.id)")


def people(conn: psycopg.Connection) -> list[dict]:
    """Return every person with a face count, when they were seen, and their clearest face."""
    rows = conn.execute(
        "select p.id, p.name, count(f.id), min(fr.captured_at), max(fr.captured_at),"
        "       (select f2.id from face f2 where f2.person_id = p.id order by f2.w * f2.h * f2.score desc limit 1)"
        "  from person p join face f on f.person_id = p.id join frame fr on fr.id = f.frame_id"
        " group by p.id order by count(f.id) desc"
    )

    return [
        {"id": person_id, "name": name, "faces": faces, "first_seen": first, "last_seen": last, "face_id": face_id}
        for person_id, name, faces, first, last, face_id in rows
    ]


def person_moments(conn: psycopg.Connection, person_id: int) -> list[str]:
    """Return the moments a person appears in."""
    rows = conn.execute(
        "select distinct fr.moment_id from face f join frame fr on fr.id = f.frame_id where f.person_id = %s",
        (person_id,),
    )

    return [moment_id for (moment_id,) in rows]


def name_person(conn: psycopg.Connection, person_id: int, name: str | None) -> bool:
    """Set or clear a person's name. Return whether they exist."""
    return conn.execute("update person set name = %s where id = %s", (name, person_id)).rowcount > 0


def merge_people(conn: psycopg.Connection, person_id: int, into: int) -> None:
    """Move every face of one person to another, then delete the first."""
    with conn.transaction():
        conn.execute("update face set person_id = %s where person_id = %s", (into, person_id))
        conn.execute("update person set name = coalesce(name, (select name from person where id = %s)) where id = %s",
                     (person_id, into))
        conn.execute("delete from person where id = %s", (person_id,))


def forget_person(conn: psycopg.Connection, person_id: int) -> bool:
    """Delete a person and every face of them. Return whether they existed."""
    return conn.execute("delete from person where id = %s", (person_id,)).rowcount > 0


def face_crop(conn: psycopg.Connection, face_id: int) -> tuple | None:
    """Return (image path, x, y, w, h) for one face."""
    return conn.execute(
        "select fr.image_path, f.x, f.y, f.w, f.h from face f join frame fr on fr.id = f.frame_id where f.id = %s",
        (face_id,),
    ).fetchone()


def new_upload(conn: psycopg.Connection, frames_per_minute: float):
    """Record a new upload and return its id."""
    (upload_id,) = conn.execute(
        "insert into upload (frames_per_minute) values (%s) returning id", (frames_per_minute,)
    ).fetchone()

    return upload_id


def update_upload(conn: psycopg.Connection, upload_id, **fields) -> None:
    """Change an upload's status, step, moment or error."""
    names = ", ".join(f"{name} = %({name})s" for name in fields)
    conn.execute(f"update upload set {names} where id = %(id)s", {**fields, "id": upload_id})


def uploads(conn: psycopg.Connection, limit: int = 20) -> list[dict]:
    """Return the most recent uploads, newest first."""
    rows = conn.execute(
        "select id, received_at, status, step, moment_id, error from upload order by received_at desc limit %s",
        (limit,),
    )

    return [
        {"id": upload_id, "received_at": received_at, "status": status, "step": step, "moment_id": moment_id,
         "error": error}
        for upload_id, received_at, status, step, moment_id, error in rows
    ]


def print_moments(rows: list[tuple]) -> None:
    print(f"{'moment':<21} {'source':<10} {'length':>7} {'frames':>7}  sound")

    for moment_id, source, _, duration, frames, has_sound, _, _ in rows:
        print(f"{moment_id:<21} {source:<10} {clock(duration):>7} {frames:>7}  {'yes' if has_sound else 'no'}")

    if not rows:
        print("(no moments yet)")


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
            saved = save_moment(conn, Path(sys.argv[2]))
            print(f"Saved {saved['frames']} frames. {saved['captions_kept']} captions kept, {saved['captions_lost']} lost.")
        elif command == "list":
            print_moments(list_moments(conn))
        else:
            print(USAGE, file=sys.stderr)
            return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
