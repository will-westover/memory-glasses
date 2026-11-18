"""The database: tables, and storing and loading what each step produces."""

import json
import sys
from datetime import datetime, timedelta
from pathlib import Path

import psycopg
from psycopg.types.json import Jsonb

from memory.citations import renumber
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


def position(frame: dict) -> tuple:
    """Return a manifest frame's (x, y, z, yaw), or Nones if the recording had no positions."""
    pose = frame.get("position")

    return tuple(pose) if pose else (None, None, None, None)


def save_moment(conn: psycopg.Connection, moment_dir: Path) -> dict:
    """Store a moment folder's manifest, keeping the captions of frames that are unchanged."""
    manifest = json.loads((moment_dir / "manifest.json").read_text())
    moment_id = manifest["id"]
    folder = f"moments/{moment_id}"

    wanted = {(f"{folder}/{frame['file']}", frame["fingerprint"]): frame for frame in manifest["frames"]}

    with conn.transaction():
        conn.execute(
            "insert into moment (id, source, started_at, duration_seconds, audio_path)"
            " values (%(id)s, %(source)s, %(started_at)s, %(duration)s, %(audio)s)"
            " on conflict (id) do update set source = excluded.source, started_at = excluded.started_at,"
            " duration_seconds = excluded.duration_seconds, audio_path = excluded.audio_path",
            {
                "id": moment_id,
                "source": manifest["source"],
                "started_at": manifest["started_at"],
                "duration": manifest["duration_seconds"],
                "audio": f"{folder}/{manifest['audio']}" if manifest["audio"] else None,
            },
        )

        conn.execute("delete from location where moment_id = %s", (moment_id,))

        if manifest.get("lat") is not None and manifest.get("lon") is not None:
            conn.execute(
                "insert into location (moment_id, at, lat, lon) values (%s, %s, %s, %s)",
                (moment_id, manifest["started_at"], manifest["lat"], manifest["lon"]),
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
                "update frame set captured_at = %s, x = %s, y = %s, z = %s, yaw = %s where id = %s",
                [(wanted[key]["captured_at"], *position(wanted[key]), stored[key][0])
                 for key in wanted.keys() & stored.keys()],
            )

            cursor.executemany(
                "insert into frame (moment_id, captured_at, image_path, fingerprint, x, y, z, yaw)"
                " values (%s, %s, %s, %s, %s, %s, %s, %s)",
                [(moment_id, wanted[key]["captured_at"], *key, *position(wanted[key]))
                 for key in wanted.keys() - stored.keys()],
            )

    return {
        "frames": len(wanted),
        "captions_kept": sum(stored[key][1] for key in wanted.keys() & stored.keys()),
        "captions_lost": sum(captioned for _, captioned in gone),
        "changed": bool(stored) and wanted.keys() != stored.keys(),
    }


FIRST_LOCATION = (
    " left join lateral (select lat, lon from location where location.moment_id = s.id order by at limit 1) l on true"
)


def list_moments(conn: psycopg.Connection) -> list[tuple]:
    """Return every moment, newest first, with where it started if known."""
    return conn.execute(
        "select s.id, s.source, s.started_at, s.duration_seconds, count(f.id), s.audio_path is not null,"
        "       l.lat, l.lon"
        f" from moment s left join frame f on f.moment_id = s.id{FIRST_LOCATION}"
        " group by s.id, l.lat, l.lon order by s.started_at desc"
    ).fetchall()


def moment_info(conn: psycopg.Connection, moment_id: str) -> tuple:
    """Return (started_at, lat, lon) for a moment, with where it started if known."""
    return conn.execute(
        f"select s.started_at, l.lat, l.lon from moment s{FIRST_LOCATION} where s.id = %s", (moment_id,)
    ).fetchone()


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
        "       case when f.x is null then null else json_build_array(f.x, f.y, f.z, f.yaw) end,"
        "       f.summary, r.name,"
        "       (select coalesce(json_agg(json_build_array(o.name, o.location)), '[]')"
        "          from frame_object o where o.frame_id = f.id),"
        "       (select coalesce(json_agg(json_build_array(t.text, t.surface)), '[]')"
        "          from frame_text t where t.frame_id = f.id)"
        "       ,(select coalesce(json_agg(distinct jsonb_build_object('id', p.id, 'name', p.name, 'face_id',"
        "          (select f2.id from face f2 where f2.person_id = p.id order by f2.w * f2.h * f2.score desc limit 1))), '[]')"
        "          from face fc join person p on p.id = fc.person_id where fc.frame_id = f.id)"
        "  from frame f join moment s on s.id = f.moment_id left join room r on r.id = f.room_id"
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
            "position": pose,
            "room": room,
        }
        for frame_id, image_path, captured_at, offset, pose, summary, room, objects, texts, people in rows
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


def gps_places(conn: psycopg.Connection) -> list[tuple]:
    """Return (name, lat, lon, radius_m) for every named place with a GPS circle."""
    return conn.execute(
        "select name, lat, lon, radius_m from place"
        " where name is not null and lat is not null and lon is not null and radius_m is not null order by name"
    ).fetchall()


def add_gps_place(conn: psycopg.Connection, name: str, lat: float, lon: float, radius_m: float) -> None:
    """Add a named place with a GPS circle, or move the one with this name."""
    moved = conn.execute(
        "update place set lat = %s, lon = %s, radius_m = %s, named = true, updated_at = now()"
        " where lower(name) = lower(%s) and radius_m is not null",
        (lat, lon, radius_m, name),
    ).rowcount

    if not moved:
        conn.execute(
            "insert into place (name, named, lat, lon, radius_m) values (%s, true, %s, %s, %s)",
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
    "select fact.id, fact.kind, fact.at, fact.moment_id, fact.offset_seconds, fact.text, frame.image_path,"
    "       frame.x, frame.z, room.name, fact.frame_id"
    "  from fact left join frame on frame.id = fact.frame_id"
    "  join moment on moment.id = fact.moment_id left join room on room.id = frame.room_id"
)


def fact_rows(rows) -> list[dict]:
    return [
        {"id": fact_id, "kind": kind, "at": at, "moment_id": moment_id, "offset_seconds": float(offset),
         "text": text, "image_path": image_path, "position": [x, z] if x is not None else None, "room": room,
         "frame_id": frame_id}
        for fact_id, kind, at, moment_id, offset, text, image_path, x, z, room, frame_id in rows
    ]


def nearby_facts(conn: psycopg.Connection, fact_id: int, metres: float, limit: int = 12) -> list[dict]:
    """Return what was seen within `metres` of where a fact's frame was taken, in the same mapped place, newest first."""
    return fact_rows(conn.execute(
        "with origin as ("
        "    select frame.x, frame.z, frame.moment_id, fs.place_id, fs.in_place_map from fact"
        "      join frame on frame.id = fact.frame_id left join frame_segment fs on fs.frame_id = frame.id"
        "     where fact.id = %(id)s and frame.x is not null)"
        f" {FACT_COLUMNS} left join frame_segment fs on fs.frame_id = frame.id, origin"
        " where frame.x is not null and fact.id <> %(id)s and fact.kind in ('frame', 'object', 'text', 'person')"
        "   and (frame.moment_id = origin.moment_id"
        "        or (fs.place_id = origin.place_id and fs.in_place_map and origin.in_place_map))"
        "   and sqrt(power(frame.x - origin.x, 2) + power(frame.z - origin.z, 2)) <= %(metres)s"
        " order by fact.at desc limit %(limit)s",
        {"id": fact_id, "metres": metres, "limit": limit},
    ))


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
    (usd,) = conn.execute("select coalesce(sum(usd), 0) from model_call where step = %s", (step,)).fetchone()

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
        "select step, count(*), sum(input_tokens), sum(output_tokens), sum(usd) from model_call"
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
    row = conn.execute("select value from user_settings where name = %s", (name,)).fetchone()

    return row[0] if row else default


def save_setting(conn: psycopg.Connection, name: str, value) -> None:
    """Save a setting."""
    conn.execute(
        "insert into user_settings (name, value) values (%s, %s) on conflict (name) do update set value = excluded.value",
        (name, str(value)),
    )


def conversations(conn: psycopg.Connection, query: str | None = None) -> list[dict]:
    """Return conversations newest first, with a title and the latest answer; only those matching `query` if given."""
    rows = conn.execute(
        "select c.id, c.updated_at,"
        "       coalesce(c.title, (select content->>'text' from message"
        "         where conversation_id = c.id and role = 'user' order by created_at limit 1)),"
        "       (select coalesce(content->'answer'->>'answer', content->>'error') from message"
        "         where conversation_id = c.id and role = 'assistant' and (content ? 'answer' or content ? 'error')"
        "         order by created_at desc limit 1)"
        "  from conversation c"
        " where exists (select 1 from message where conversation_id = c.id and role = 'user')"
        "   and (%(query)s::text is null or c.title ilike %(like)s or exists (select 1 from message"
        "         where conversation_id = c.id and role in ('user', 'assistant') and content::text ilike %(like)s))"
        " order by c.updated_at desc",
        {"query": query, "like": f"%{query}%" if query else None},
    )

    return [
        {"id": conversation_id, "updated_at": updated_at, "title": title, "preview": preview or ""}
        for conversation_id, updated_at, title, preview in rows
    ]


def set_title(conn: psycopg.Connection, conversation_id, title: str) -> None:
    """Give a conversation its title."""
    conn.execute("update conversation set title = %s where id = %s", (title, conversation_id))


def conversation_title(conn: psycopg.Connection, conversation_id) -> str | None:
    """Return a conversation's title, if it has one."""
    row = conn.execute("select title from conversation where id = %s", (conversation_id,)).fetchone()

    return row[0] if row else None


def turn_from(question: dict, replies: list[dict]) -> dict:
    """Work out one question's answer, sources, problem, calls, time and cost from the messages that followed it."""
    facts: dict = {fact["id"]: fact for fact in question["content"].get("facts", [])}
    answer, answered_at, problem = None, None, None

    for message in replies:
        content = message["content"]
        facts.update({fact["id"]: fact for fact in content.get("facts", [])})

        if "answer" in content:
            answer, answered_at = content["answer"], message["created_at"]

        if "error" in content:
            problem = content["error"]

    text, kept = renumber(answer["answer"], answer["sources"], facts) if answer else (None, [])
    sources = [{**facts[fact_id], "at": datetime.fromisoformat(facts[fact_id]["at"]),
                "offset_seconds": float(facts[fact_id]["offset_seconds"])} for fact_id in kept]
    highlight = answer.get("highlight") if answer else None

    return {
        "id": question["id"],
        "asked_at": question["created_at"],
        "question": question["content"].get("text", ""),
        "answer": text,
        "found": answer["found"] if answer else None,
        "problem": problem if answer is None else None,
        "model_calls": sum(message["model_calls"] for message in replies),
        "seconds": (answered_at - question["created_at"]).total_seconds() if answered_at else None,
        "usd": sum(message["usd"] for message in replies),
        "sources": sources,
        "highlight": kept.index(highlight) if highlight in kept else None,
        "highlight_reason": answer.get("highlight_reason") if highlight in kept else None,
        "layout": (answer or {}).get("layout") or "text",
        "follow_ups": (answer or {}).get("follow_ups") or [],
        "next_steps": (answer or {}).get("next_steps") or [] if answer and not answer["found"] else [],
    }


def turns_from(messages: list[dict]) -> list[dict]:
    """Split a conversation's messages, oldest first, into turns: each question and what followed it."""
    groups: list[tuple[dict, list]] = []

    for message in messages:
        if message["role"] == "user":
            groups.append((message, []))
        elif groups:
            groups[-1][1].append(message)

    return [turn_from(question, replies) for question, replies in groups]


def conversation_turns(conn: psycopg.Connection, conversation_id) -> list[dict] | None:
    """Return a conversation's turns, oldest first, each with its sources; None if there is no such conversation."""
    if not conn.execute("select 1 from conversation where id = %s", (conversation_id,)).fetchone():
        return None

    rows = conn.execute(
        "select m.id, m.created_at, m.role, m.content,"
        "       count(c.id) filter (where c.step not like 'embed%%'), coalesce(sum(c.usd), 0)"
        "  from message m left join model_call c on c.message_id = m.id"
        " where m.conversation_id = %s group by m.id order by m.created_at, m.role <> 'user'",
        (conversation_id,),
    ).fetchall()

    return turns_from([
        {"id": message_id, "created_at": created_at, "role": role, "content": content, "model_calls": calls,
         "usd": float(usd)}
        for message_id, created_at, role, content, calls, usd in rows
    ])


def new_conversation(conn: psycopg.Connection):
    """Create an empty conversation and return its id."""
    (conversation_id,) = conn.execute("insert into conversation default values returning id").fetchone()

    return conversation_id


def save_messages(conn: psycopg.Connection, conversation_id, messages: list[dict]):
    """Store messages as they were sent and received, linking each to the model calls that made it. Return the first id."""
    ids = []

    with conn.transaction():
        for message in messages:
            (message_id,) = conn.execute(
                "insert into message (conversation_id, created_at, role, content) values (%s, %s, %s, %s) returning id",
                (conversation_id, message["at"], message["role"], Jsonb(message["content"])),
            ).fetchone()
            ids.append(message_id)

            if message.get("calls"):
                conn.execute("update model_call set message_id = %s where id = any(%s)", (message_id, message["calls"]))

        conn.execute("update conversation set updated_at = now() where id = %s", (conversation_id,))

    return ids[0]


def delete_conversation(conn: psycopg.Connection, conversation_id) -> bool:
    """Delete a conversation and everything in it. Return whether it existed."""
    return conn.execute("delete from conversation where id = %s", (conversation_id,)).rowcount > 0


def frame_fact(conn: psycopg.Connection, frame_id: int) -> dict | None:
    """Return the fact that describes a frame as a whole."""
    found = fact_rows(conn.execute(f"{FACT_COLUMNS} where fact.frame_id = %s and fact.kind = 'frame' limit 1",
                                   (frame_id,)))

    return found[0] if found else None


def source_details(conn: psycopg.Connection, facts: list[dict]) -> dict:
    """Return, for each fact id, the people in its frame, its place, and for speech its speaker and end."""
    ids = [fact["id"] for fact in facts]
    rows = conn.execute(
        "select f.id, f.frame_id, p.id, p.name, p.lat, p.lon,"
        "       (select coalesce(json_agg(distinct jsonb_build_object('id', pe.id, 'name', pe.name)), '[]')"
        "          from face fa join person pe on pe.id = fa.person_id where fa.frame_id = f.frame_id),"
        "       (select a.speaker from audio_segment a where a.moment_id = f.moment_id"
        "         and a.offset_seconds >= f.offset_seconds - 0.01 order by a.offset_seconds limit 1),"
        "       (select max(a.offset_seconds + extract(epoch from a.end_at - a.start_at)) from audio_segment a"
        "         where a.moment_id = f.moment_id and a.offset_seconds >= f.offset_seconds - 0.01"
        "           and a.offset_seconds < coalesce((select min(n.offset_seconds) from fact n"
        "                where n.moment_id = f.moment_id and n.kind = f.kind and n.offset_seconds > f.offset_seconds),"
        "               'infinity'))"
        "  from fact f"
        "  left join segment s on s.moment_id = f.moment_id and f.at between s.start_at and s.end_at"
        "  left join place p on p.id = s.place_id"
        " where f.id = any(%s)",
        (ids,),
    )

    return {
        fact_id: {"people": people, "speaker": speaker, "end_seconds": float(end) if end is not None else None,
                  "place": {"id": place_id, "name": name, "lat": lat, "lon": lon} if place_id else None}
        for fact_id, _, place_id, name, lat, lon, people, speaker, end in rows
    }


def recent_facts(conn: psycopg.Connection, limit: int = 40) -> list[dict]:
    """Return the newest summaries, speech, text and people facts, newest first."""
    return fact_rows(conn.execute(
        f"{FACT_COLUMNS} where fact.kind in ('summary', 'said', 'text', 'person') order by fact.at desc limit %s",
        (limit,),
    ))


def latest_moment_id(conn: psycopg.Connection) -> str | None:
    """Return the id of the newest moment."""
    row = conn.execute("select id from moment order by started_at desc limit 1").fetchone()

    return row[0] if row else None


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


def moment_place(conn: psycopg.Connection, moment_id: str) -> dict | None:
    """Return the place of a moment's first segment, if known, and whether its positions are in the place's map."""
    row = conn.execute(
        "select p.id, p.name, s.in_place_map from segment s join place p on p.id = s.place_id"
        " where s.moment_id = %s order by s.start_at limit 1",
        (moment_id,),
    ).fetchone()

    return {"id": row[0], "name": row[1], "in_map": row[2]} if row else None


def located_moments(conn: psycopg.Connection) -> list[dict]:
    """Return every moment that has a location, newest first, with where it started and its place if known."""
    rows = conn.execute(
        "select s.id, s.source, s.started_at, s.duration_seconds, l.lat, l.lon, p.id, p.name"
        f"  from moment s{FIRST_LOCATION}"
        "  left join lateral (select place_id from segment where segment.moment_id = s.id order by start_at limit 1) g"
        "    on true"
        "  left join place p on p.id = g.place_id"
        " where l.lat is not null order by s.started_at desc"
    )

    return [
        {"id": moment_id, "source": source, "started_at": started_at, "duration_seconds": duration, "lat": lat,
         "lon": lon, "place": {"id": place_id, "name": name} if place_id else None}
        for moment_id, source, started_at, duration, lat, lon, place_id, name in rows
    ]


def places(conn: psycopg.Connection) -> list[dict]:
    """Return every place, most recently updated first, with its rooms and how many moments were there."""
    rows = conn.execute(
        "select p.id, p.name, p.lat, p.lon, p.updated_at, p.map_path is not null,"
        "       (select count(distinct s.moment_id) from segment s where s.place_id = p.id),"
        "       (select coalesce(json_agg(r.name order by r.name), '[]') from room r where r.place_id = p.id)"
        "  from place p order by p.updated_at desc"
    )

    return [
        {"id": place_id, "name": name, "lat": lat, "lon": lon, "updated_at": updated_at, "has_map": has_map,
         "moments": moments, "rooms": rooms}
        for place_id, name, lat, lon, updated_at, has_map, moments, rooms in rows
    ]


def place_map(conn: psycopg.Connection, place_id: int) -> str | None:
    """Return the path of a place's saved ARKit map."""
    row = conn.execute("select map_path from place where id = %s", (place_id,)).fetchone()

    return row[0] if row else None


def save_place(conn: psycopg.Connection, place_id: int | None, map_path: str | None, lat, lon) -> int:
    """Create a place, or give an existing one a newer map. Return its id."""
    if place_id is not None:
        conn.execute("update place set map_path = coalesce(%s, map_path), updated_at = now() where id = %s",
                     (map_path, place_id))
        return place_id

    (new_id,) = conn.execute(
        "insert into place (map_path, lat, lon) values (%s, %s, %s) returning id", (map_path, lat, lon)
    ).fetchone()

    return new_id


def set_moment_place(conn: psycopg.Connection, moment_id: str, place_id: int, in_map: bool) -> None:
    """Make a moment one segment at a place, from its start to its last frame."""
    conn.execute("delete from segment where moment_id = %s", (moment_id,))
    conn.execute(
        "insert into segment (moment_id, place_id, start_at, end_at, in_place_map)"
        " select m.id, %s, m.started_at,"
        "        greatest(m.started_at + make_interval(secs => m.duration_seconds),"
        "                 coalesce(max(f.captured_at), m.started_at)), %s"
        "   from moment m left join frame f on f.moment_id = m.id where m.id = %s group by m.id",
        (place_id, in_map, moment_id),
    )


def name_place(conn: psycopg.Connection, place_id: int, name: str | None, by_user: bool) -> bool:
    """Set a place's name, unless a model is naming one the user already named. Return whether it changed."""
    return conn.execute(
        "update place set name = %s, named = named or %s where id = %s and (%s or not named)",
        (name, by_user, place_id, by_user),
    ).rowcount > 0


def place_moments(conn: psycopg.Connection, place_id: int) -> list[str]:
    """Return the moments with a segment at a place, newest first."""
    rows = conn.execute(
        "select m.id from moment m where exists (select 1 from segment s where s.moment_id = m.id and s.place_id = %s)"
        " order by m.started_at desc",
        (place_id,),
    )

    return [moment_id for (moment_id,) in rows]


def place_vectors(conn: psycopg.Connection) -> dict[int, str]:
    """Return each place's average frame embedding, as pgvector text."""
    rows = conn.execute(
        "select fs.place_id, avg(f.embedding)::text from fact f join frame_segment fs on fs.frame_id = f.frame_id"
        " where f.kind = 'frame' and fs.place_id is not null group by fs.place_id"
    )

    return dict(rows.fetchall())


def place_rooms(conn: psycopg.Connection, place_id: int) -> list[dict]:
    """Return a place's rooms with how many frames were taken in each."""
    rows = conn.execute(
        "select r.id, r.name, r.description, r.named, (select count(*) from frame f where f.room_id = r.id)"
        "  from room r where r.place_id = %s order by r.name",
        (place_id,),
    )

    return [
        {"id": room_id, "name": name, "description": description, "named": named, "frames": frames}
        for room_id, name, description, named, frames in rows
    ]


def save_room(conn: psycopg.Connection, place_id: int, name: str, description: str | None) -> int:
    """Return the id of the place's room with this name, creating it if needed."""
    row = conn.execute(
        "select id from room where place_id = %s and lower(name) = lower(%s)", (place_id, name)
    ).fetchone()

    if row:
        conn.execute("update room set description = coalesce(description, %s) where id = %s", (description, row[0]))
        return row[0]

    (room_id,) = conn.execute(
        "insert into room (place_id, name, description) values (%s, %s, %s) returning id", (place_id, name, description)
    ).fetchone()

    return room_id


def room_examples(conn: psycopg.Connection, room_id: int, limit: int = 3) -> list[str]:
    """Return the first sentences of a few frames taken in a room, spread over its moments."""
    rows = conn.execute(
        "select split_part(summary, '. ', 1) from frame where room_id = %s order by md5(id::text) limit %s",
        (room_id, limit),
    )

    return [summary for (summary,) in rows]


def set_frame_rooms(conn: psycopg.Connection, moment_id: str, rooms: dict[int, int]) -> None:
    """Set the room of each of a moment's frames, clearing the rest."""
    conn.execute("update frame set room_id = null where moment_id = %s", (moment_id,))

    with conn.cursor() as cursor:
        cursor.executemany("update frame set room_id = %s where id = %s", [(room, frame) for frame, room in rooms.items()])


def mapped_frames(conn: psycopg.Connection, place_id: int, moment_id: str) -> list[tuple]:
    """Return (x, z, room id) for frames of other moments that share the place's map and already have a room."""
    return conn.execute(
        "select f.x, f.z, f.room_id from frame f join frame_segment fs on fs.frame_id = f.id"
        " where fs.place_id = %s and fs.in_place_map and f.moment_id <> %s and f.x is not null and f.room_id is not null",
        (place_id, moment_id),
    ).fetchall()


def drop_empty(conn: psycopg.Connection) -> None:
    """Delete rooms no frame is in and places nothing happened at, unless the user named them."""
    conn.execute("delete from room r where not r.named and not exists (select 1 from frame f where f.room_id = r.id)")
    conn.execute("delete from place p where not p.named and not exists (select 1 from segment s where s.place_id = p.id)")


def name_room(conn: psycopg.Connection, room_id: int, name: str) -> int | None:
    """Rename a room as the user asked. Return its place's id, or None if there is no such room."""
    row = conn.execute("update room set name = %s, named = true where id = %s returning place_id",
                       (name, room_id)).fetchone()

    return row[0] if row else None


def new_job(conn: psycopg.Connection, frames_per_minute: float):
    """Record a new processing job and return its id."""
    (job_id,) = conn.execute(
        "insert into job (frames_per_minute) values (%s) returning id", (frames_per_minute,)
    ).fetchone()

    return job_id


def update_job(conn: psycopg.Connection, job_id, **fields) -> None:
    """Change a job's status, step, moment or error."""
    names = ", ".join(f"{name} = %({name})s" for name in fields)
    conn.execute(f"update job set {names} where id = %(id)s", {**fields, "id": job_id})


def jobs(conn: psycopg.Connection, limit: int = 20) -> list[dict]:
    """Return the most recent jobs, newest first."""
    rows = conn.execute(
        "select id, received_at, status, step, moment_id, error from job order by received_at desc limit %s",
        (limit,),
    )

    return [
        {"id": job_id, "received_at": received_at, "status": status, "step": step, "moment_id": moment_id,
         "error": error}
        for job_id, received_at, status, step, moment_id, error in rows
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
