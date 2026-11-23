"""The things and people seen, each sighting of them, and the scenes of each moment."""

from datetime import timedelta

import psycopg


def lock_moment(conn: psycopg.Connection, moment_id: str) -> None:
    """Make another rebuild of this moment's facts wait until this transaction ends."""
    conn.execute("select pg_advisory_xact_lock(hashtext('facts:' || %s))", (moment_id,))


def forget_world(conn: psycopg.Connection, moment_id: str) -> None:
    """Delete a moment's scenes and sightings, and the things it saw that are no longer seen anywhere."""
    seen = [
        entity_id
        for (entity_id,) in conn.execute(
            "delete from sighting where frame_id in (select id from frame where moment_id = %s) returning entity_id",
            (moment_id,),
        )
    ]
    conn.execute("delete from episode where moment_id = %s", (moment_id,))
    conn.execute(
        "delete from entity e where e.id = any(%s) and e.person_id is null"
        " and not exists (select 1 from sighting s where s.entity_id = e.id)",
        (list(set(seen)),),
    )


def save_scene(conn: psycopg.Connection, moment_id: str, scene: dict) -> int:
    """Store one scene and return its id."""
    end_at = scene["at"] + timedelta(seconds=scene["end_offset_seconds"] - scene["offset_seconds"])

    return conn.execute(
        "insert into episode (moment_id, level, start_at, end_at, title, summary) values (%s, 'scene', %s, %s, %s, %s)"
        " returning id",
        (moment_id, scene["at"], end_at, scene.get("title"), scene["text"]),
    ).fetchone()[0]


def new_thing(conn: psycopg.Connection, name: str, details: str | None, frame_id: int, box: list | None) -> int:
    """Store a newly seen thing, with the photo and box that show it best, and return its id."""
    return conn.execute(
        "insert into entity (kind, name, details, crop_frame_id, crop_box) values ('object', %s, %s, %s, %s) returning id",
        (name, details or None, frame_id, box),
    ).fetchone()[0]


def person_entity(conn: psycopg.Connection, person_id: int, name: str | None) -> int:
    """Return the entity of a recognised person, making it the first time they are seen."""
    return conn.execute(
        "insert into entity (kind, name, person_id) values ('person', %s, %s)"
        " on conflict (person_id) where person_id is not null do update set name = excluded.name returning id",
        (name or "Someone", person_id),
    ).fetchone()[0]


def save_sightings(conn: psycopg.Connection, rows: list[tuple]) -> None:
    """Store sightings as (entity, frame, scene, at, where, details, box, source), then update when each was seen."""
    with conn.cursor() as cursor:
        cursor.executemany(
            "insert into sighting (entity_id, frame_id, episode_id, at, place, details, box, source)"
            " values (%s, %s, %s, %s, %s, %s, %s, %s)",
            rows,
        )

    conn.execute(
        "update entity e set first_seen = s.first, last_seen = s.last"
        " from (select entity_id, min(at) as first, max(at) as last from sighting where entity_id = any(%(ids)s)"
        "       group by entity_id) s"
        " where s.entity_id = e.id",
        {"ids": list({row[0] for row in rows})},
    )


def save_thing_vectors(conn: psycopg.Connection, vectors: dict[int, str]) -> None:
    """Store the embedding of each thing's name and details."""
    with conn.cursor() as cursor:
        cursor.executemany(
            "update entity set embedding = %s::halfvec where id = %s",
            [(vector, entity_id) for entity_id, vector in vectors.items()],
        )


def moment_things(conn: psycopg.Connection, moment_id: str) -> list[dict]:
    """Return the things first recorded in a moment that have a photo crop, with that photo."""
    rows = conn.execute(
        "select distinct e.id, e.name, e.details, f.image_path, e.crop_box from entity e"
        " join frame f on f.id = e.crop_frame_id"
        " where e.kind = 'object' and e.merged_into is null and e.crop_box is not null and f.moment_id = %s",
        (moment_id,),
    )

    return [
        {"id": entity_id, "name": name, "details": details, "image_path": image_path, "box": box}
        for entity_id, name, details, image_path, box in rows
    ]


LINK_CANDIDATES_SQL = """
    select e.id, e.name, e.details, f.image_path, e.crop_box, 1 - (e.embedding <=> n.embedding) as similarity
      from entity n, entity e join frame f on f.id = e.crop_frame_id
     where n.id = %(id)s and e.id <> n.id and e.kind = 'object' and e.merged_into is null
       and e.embedding is not null and e.crop_box is not null and f.moment_id <> %(moment)s
       and (%(place)s::bigint is null or exists (select 1 from sighting s join frame_segment fs on fs.frame_id = s.frame_id
                                                where s.entity_id = e.id and fs.place_id = %(place)s))
     order by e.embedding <=> n.embedding
     limit %(limit)s
"""


def link_candidates(
    conn: psycopg.Connection, entity_id: int, moment_id: str, place_id: int | None, limit: int
) -> list[dict]:
    """Return the earlier things from other moments whose name and details read most alike: only those seen at the same
    place when the place is known."""
    conn.execute("set local hnsw.iterative_scan = relaxed_order")
    rows = conn.execute(
        LINK_CANDIDATES_SQL, {"id": entity_id, "moment": moment_id, "place": place_id, "limit": limit}
    ).fetchall()

    return [
        {"id": other, "name": name, "details": details, "image_path": image_path, "box": box, "similarity": similarity}
        for other, name, details, image_path, box, similarity in sorted(rows, key=lambda row: -row[5])
    ]


def link_things(conn: psycopg.Connection, entity_id: int, same_as: int, reason: str) -> None:
    """Record that a thing is the same as an earlier one; the link can be undone, nothing is moved."""
    conn.execute("update entity set merged_into = %s, merge_reason = %s where id = %s", (same_as, reason, entity_id))
    conn.execute(
        "update entity set first_seen = least(first_seen, (select first_seen from entity where id = %(new)s)),"
        " last_seen = greatest(last_seen, (select last_seen from entity where id = %(new)s)) where id = %(root)s",
        {"new": entity_id, "root": same_as},
    )


def coverage(conn: psycopg.Connection, start, end, moments: list[str] | None = None) -> list[dict]:
    """Return the recordings that overlap a time range, oldest first, each with its scenes, title and times."""
    rows = conn.execute(
        "select m.id, m.title, m.overview, m.started_at, m.started_at + make_interval(secs => m.duration_seconds),"
        "       coalesce((select json_agg(json_build_object('title', e.title, 'start', e.start_at, 'end', e.end_at)"
        "                 order by e.start_at) from episode e where e.moment_id = m.id and e.level = 'scene'), '[]')"
        "  from moment m"
        " where m.started_at <= %(end)s and m.started_at + make_interval(secs => m.duration_seconds) >= %(start)s"
        "   and (%(moments)s::text[] is null or m.id = any(%(moments)s::text[]))"
        " order by m.started_at",
        {"start": start, "end": end, "moments": moments},
    )

    return [
        {"id": moment_id, "title": title, "overview": overview, "start": began, "end": ended, "scenes": scenes}
        for moment_id, title, overview, began, ended, scenes in rows
    ]


def moment_scenes(conn: psycopg.Connection, moment_id: str) -> list[dict]:
    """Return a moment's scenes in order, each with its times, title, summary, people and frames."""
    rows = conn.execute(
        "select e.id, e.start_at, e.end_at, e.title, e.summary,"
        "       coalesce((select array_agg(distinct en.name) from sighting s join entity en on en.id = s.entity_id"
        "                  where s.episode_id = e.id and en.kind = 'person'), '{}'),"
        "       coalesce((select array_agg(distinct s.frame_id) from sighting s where s.episode_id = e.id), '{}')"
        "  from episode e where e.moment_id = %s and e.level = 'scene' order by e.start_at",
        (moment_id,),
    )

    return [
        {
            "id": scene_id,
            "start": start,
            "end": end,
            "title": title,
            "summary": summary,
            "people": people,
            "frames": frames,
        }
        for scene_id, start, end, title, summary, people, frames in rows
    ]


def save_activity(
    conn: psycopg.Connection, moment_id: str, start, end, title: str, summary: str, scene_ids: list[int]
) -> int:
    """Store one activity of a moment, make its scenes part of it, and return its id."""
    activity_id = conn.execute(
        "insert into episode (moment_id, level, start_at, end_at, title, summary) values (%s, 'activity', %s, %s, %s, %s)"
        " returning id",
        (moment_id, start, end, title, summary),
    ).fetchone()[0]
    conn.execute("update episode set parent_id = %s where id = any(%s)", (activity_id, scene_ids))

    return activity_id


def forget_activities(conn: psycopg.Connection, moment_id: str) -> None:
    """Delete a moment's activities, keeping its scenes."""
    conn.execute("update episode set parent_id = null where moment_id = %s and level = 'scene'", (moment_id,))
    conn.execute("delete from episode where moment_id = %s and level = 'activity'", (moment_id,))


def day_activities(conn: psycopg.Connection, start, end) -> list[dict]:
    """Return every activity that starts between two times, in order, with the moment it belongs to."""
    rows = conn.execute(
        "select moment_id, start_at, end_at, title, summary from episode"
        " where level = 'activity' and start_at >= %s and start_at < %s order by start_at",
        (start, end),
    )

    return [{"moment_id": m, "start": s, "end": e, "title": t, "summary": su} for m, s, e, t, su in rows]


def save_day(conn: psycopg.Connection, day, start, end, title: str, summary: str) -> int:
    """Store the summary of a day, replacing any earlier one, and return its id."""
    conn.execute("delete from episode where level = 'day' and day = %s", (day,))

    return conn.execute(
        "insert into episode (level, day, start_at, end_at, title, summary) values ('day', %s, %s, %s, %s, %s) returning id",
        (day, start, end, title, summary),
    ).fetchone()[0]


def forget_day_facts(conn: psycopg.Connection, moment_ids: list[str]) -> None:
    """Delete the day facts stored on any of these moments."""
    conn.execute("delete from fact where kind = 'day' and moment_id = any(%s)", (moment_ids,))
