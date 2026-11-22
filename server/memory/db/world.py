"""The things and people seen, each sighting of them, and the scenes of each moment."""

from datetime import timedelta

import psycopg


def lock_moment(conn: psycopg.Connection, moment_id: str) -> None:
    """Make another rebuild of this moment's facts wait until this transaction ends."""
    conn.execute("select pg_advisory_xact_lock(hashtext('facts:' || %s))", (moment_id,))


def forget_world(conn: psycopg.Connection, moment_id: str) -> None:
    """Delete a moment's scenes and sightings, and things no longer seen anywhere that nobody confirmed."""
    conn.execute("delete from sighting where frame_id in (select id from frame where moment_id = %s)", (moment_id,))
    conn.execute("delete from episode where moment_id = %s", (moment_id,))
    conn.execute(
        "delete from entity e where e.person_id is null and not e.confirmed"
        " and not exists (select 1 from sighting s where s.entity_id = e.id)"
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
        " from (select entity_id, min(at) as first, max(at) as last from sighting group by entity_id) s"
        " where s.entity_id = e.id and e.id = any(%s)",
        (list({row[0] for row in rows}),),
    )
