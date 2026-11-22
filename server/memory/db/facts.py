"""Facts, the rows search returns, and each moment's overview."""

from datetime import timedelta

import psycopg

from memory.config import SUMMARY_STRETCH_SECONDS


def replace_facts(conn: psycopg.Connection, moment_id: str, facts: list[dict], vectors: list[str]) -> None:
    """Store a moment's facts, replacing any earlier ones; two rebuilds of one moment wait for each other."""
    conn.execute("select pg_advisory_xact_lock(hashtext('facts:' || %s))", (moment_id,))
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


FACT_SELECT = (
    "select fact.id, fact.kind, fact.at, fact.moment_id, fact.offset_seconds, fact.text,"
    "       coalesce(frame.image_path, best.image_path, closest.image_path), frame.x, frame.z, room.name,"
    "       fact.frame_id"
    "  from fact left join frame on frame.id = fact.frame_id"
    "  join moment on moment.id = fact.moment_id left join room on room.id = frame.room_id"
    "  left join lateral ("
    "    select seen.image_path from fact shown join frame seen on seen.id = shown.frame_id"
    "     where fact.frame_id is null and shown.moment_id = fact.moment_id and shown.kind = 'frame'"
    "       and shown.at between fact.at - interval '10 seconds' and fact.at + interval '70 seconds'"
    "     order by shown.embedding <=> fact.embedding limit 1"
    "  ) best on true"
    "  left join lateral ("
    "    select near.image_path from frame near"
    "     where fact.frame_id is null and best.image_path is null and near.moment_id = fact.moment_id"
    "       and near.captioned_at is not null"
    "     order by abs(extract(epoch from near.captured_at - fact.at)) limit 1"
    "  ) closest on true"
)


def fact_rows(rows) -> list[dict]:
    return [
        {
            "id": fact_id,
            "kind": kind,
            "at": at,
            "moment_id": moment_id,
            "offset_seconds": float(offset),
            "text": text,
            "image_path": image_path,
            "position": [x, z] if x is not None else None,
            "room": room,
            "frame_id": frame_id,
        }
        for fact_id, kind, at, moment_id, offset, text, image_path, x, z, room, frame_id in rows
    ]


def nearby_facts(conn: psycopg.Connection, fact_id: int, metres: float, limit: int = 12) -> list[dict]:
    """Return what was seen within `metres` of where a fact's frame was taken, in the same mapped place, newest first."""
    return fact_rows(
        conn.execute(
            "with origin as ("
            "    select frame.x, frame.z, frame.moment_id, fs.place_id, fs.in_place_map from fact"
            "      join frame on frame.id = fact.frame_id left join frame_segment fs on fs.frame_id = frame.id"
            "     where fact.id = %(id)s and frame.x is not null)"
            f" {FACT_SELECT} left join frame_segment fs on fs.frame_id = frame.id, origin"
            " where frame.x is not null and fact.id <> %(id)s and fact.kind in ('frame', 'object', 'text', 'person')"
            "   and (frame.moment_id = origin.moment_id"
            "        or (fs.place_id = origin.place_id and fs.in_place_map and origin.in_place_map))"
            "   and sqrt(power(frame.x - origin.x, 2) + power(frame.z - origin.z, 2)) <= %(metres)s"
            " order by fact.at desc limit %(limit)s",
            {"id": fact_id, "metres": metres, "limit": limit},
        )
    )


def facts_by_id(conn: psycopg.Connection, fact_ids: list[int]) -> list[dict]:
    """Return the facts with these ids."""
    return fact_rows(conn.execute(f"{FACT_SELECT} where fact.id = any(%s)", (fact_ids,)))


def timeline(conn: psycopg.Connection, start, end, moments: list[str] | None = None) -> list[dict]:
    """Return the frames, speech and summaries between two times, in order, from only the given moments if named."""
    return fact_rows(
        conn.execute(
            f"{FACT_SELECT} where fact.at <= %(end)s"
            " and (%(moments)s::text[] is null or fact.moment_id = any(%(moments)s::text[])) and ("
            "    (fact.kind in ('frame', 'speech') and fact.at >= %(start)s)"
            " or (fact.kind = 'summary' and fact.at > %(summary_start)s))"
            " order by fact.at, fact.id",
            {
                "start": start,
                "end": end,
                "summary_start": start - timedelta(seconds=SUMMARY_STRETCH_SECONDS),
                "moments": moments,
            },
        )
    )


def save_overview(conn: psycopg.Connection, moment_id: str, title: str, overview: str, chapters: dict) -> None:
    """Store a moment's title and overview, and the chapter title of each of its summaries."""
    conn.execute("update moment set title = %s, overview = %s where id = %s", (title, overview, moment_id))

    with conn.cursor() as cursor:
        cursor.executemany(
            "update fact set title = %s where id = %s", [(name, fact_id) for fact_id, name in chapters.items()]
        )


def moments_without_title(conn: psycopg.Connection) -> list[str]:
    """Return the moments that have summaries but no title yet, oldest first."""
    rows = conn.execute(
        "select m.id from moment m where m.title is null"
        " and exists (select 1 from fact f where f.moment_id = m.id and f.kind = 'summary') order by m.started_at"
    )

    return [moment_id for (moment_id,) in rows]


def moment_overview(conn: psycopg.Connection, moment_id: str) -> tuple:
    """Return (title, overview) for a moment."""
    return conn.execute("select title, overview from moment where id = %s", (moment_id,)).fetchone() or (None, None)


def chapter_titles(conn: psycopg.Connection, moment_id: str) -> dict[int, str]:
    """Return the chapter title of each of a moment's summaries, by fact id."""
    rows = conn.execute(
        "select id, title from fact where moment_id = %s and kind = 'summary' and title is not null", (moment_id,)
    )

    return dict(rows.fetchall())


def summaries(conn: psycopg.Connection, moment_id: str) -> list[dict]:
    """Return a moment's summary facts, in order."""
    return fact_rows(
        conn.execute(
            f"{FACT_SELECT} where fact.moment_id = %s and fact.kind = 'summary' order by fact.at", (moment_id,)
        )
    )


def frame_fact(conn: psycopg.Connection, frame_id: int) -> dict | None:
    """Return the fact that describes a frame as a whole."""
    found = fact_rows(
        conn.execute(f"{FACT_SELECT} where fact.frame_id = %s and fact.kind = 'frame' limit 1", (frame_id,))
    )

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
        fact_id: {
            "people": people,
            "speaker": speaker,
            "end_seconds": float(end) if end is not None else None,
            "place": {"id": place_id, "name": name, "lat": lat, "lon": lon} if place_id else None,
        }
        for fact_id, _, place_id, name, lat, lon, people, speaker, end in rows
    }


def recent_facts(conn: psycopg.Connection, limit: int = 40) -> list[dict]:
    """Return the newest summaries, speech, text and people facts, newest first."""
    return fact_rows(
        conn.execute(
            f"{FACT_SELECT} where fact.kind in ('summary', 'said', 'text', 'person') order by fact.at desc limit %s",
            (limit,),
        )
    )
