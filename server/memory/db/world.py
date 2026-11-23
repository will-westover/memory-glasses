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


def save_thing_vectors(conn: psycopg.Connection, vectors: dict[int, str]) -> None:
    """Store the embedding of each thing's name and details."""
    with conn.cursor() as cursor:
        cursor.executemany(
            "update entity set embedding = %s::vector where id = %s",
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


def link_candidates(
    conn: psycopg.Connection, entity_id: int, moment_id: str, place_id: int | None, limit: int
) -> list[dict]:
    """Return earlier things from other moments whose name and details read most alike, those at the same place first,
    leaving out any pair you said are not the same."""
    rows = conn.execute(
        "select e.id, e.name, e.details, f.image_path, e.crop_box, 1 - (e.embedding <=> n.embedding)"
        "  from entity n, entity e join frame f on f.id = e.crop_frame_id"
        " where n.id = %(id)s and e.id <> n.id and e.kind = 'object' and e.merged_into is null"
        "   and e.embedding is not null and e.crop_box is not null and f.moment_id <> %(moment)s"
        "   and not exists (select 1 from not_same x where (x.frame_id, x.other_frame_id) in"
        "                   ((n.crop_frame_id, e.crop_frame_id), (e.crop_frame_id, n.crop_frame_id)))"
        " order by exists (select 1 from sighting s join frame_segment fs on fs.frame_id = s.frame_id"
        "                  where s.entity_id = e.id and fs.place_id = %(place)s) desc,"
        "          e.embedding <=> n.embedding"
        " limit %(limit)s",
        {"id": entity_id, "moment": moment_id, "place": place_id, "limit": limit},
    )

    return [
        {"id": other, "name": name, "details": details, "image_path": image_path, "box": box, "similarity": similarity}
        for other, name, details, image_path, box, similarity in rows
    ]


def link_things(conn: psycopg.Connection, entity_id: int, same_as: int, reason: str) -> None:
    """Record that a thing is the same as an earlier one; the link can be undone, nothing is moved."""
    conn.execute("update entity set merged_into = %s, merge_reason = %s where id = %s", (same_as, reason, entity_id))
    conn.execute(
        "update entity set first_seen = least(first_seen, (select first_seen from entity where id = %(new)s)),"
        " last_seen = greatest(last_seen, (select last_seen from entity where id = %(new)s)) where id = %(root)s",
        {"new": entity_id, "root": same_as},
    )


def unlink_thing(conn: psycopg.Connection, entity_id: int) -> bool:
    """Undo a link, so the thing stands on its own again, and remember that the two photos show different things."""
    conn.execute(
        "insert into not_same (frame_id, other_frame_id)"
        " select e.crop_frame_id, r.crop_frame_id from entity e join entity r on r.id = e.merged_into"
        " where e.id = %s and e.crop_frame_id is not null and r.crop_frame_id is not null on conflict do nothing",
        (entity_id,),
    )

    return (
        conn.execute(
            "update entity set merged_into = null, merge_reason = null, confirmed = true"
            " where id = %s and merged_into is not null",
            (entity_id,),
        ).rowcount
        > 0
    )


def confirm_link(conn: psycopg.Connection, entity_id: int) -> bool:
    """Mark a link as checked by you."""
    return (
        conn.execute(
            "update entity set confirmed = true where id = %s and merged_into is not null", (entity_id,)
        ).rowcount
        > 0
    )


def recent_links(conn: psycopg.Connection, limit: int = 50) -> list[dict]:
    """Return the newest links between things, with each side's name and photo, for you to check."""
    rows = conn.execute(
        "select e.id, e.name, fe.image_path, e.crop_box, r.id, r.name, fr.image_path, r.crop_box, e.merge_reason, e.confirmed"
        "  from entity e join entity r on r.id = e.merged_into"
        "  left join frame fe on fe.id = e.crop_frame_id left join frame fr on fr.id = r.crop_frame_id"
        " order by e.id desc limit %s",
        (limit,),
    )

    return [
        {
            "id": entity_id,
            "name": name,
            "image_path": image_path,
            "box": box,
            "same_as": {"id": root_id, "name": root_name, "image_path": root_image, "box": root_box},
            "reason": reason,
            "confirmed": confirmed,
        }
        for entity_id, name, image_path, box, root_id, root_name, root_image, root_box, reason, confirmed in rows
    ]


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
