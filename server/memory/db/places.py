"""GPS places, places, rooms and segments."""

import psycopg

from memory.db.moments import FIRST_LOCATION


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
        {
            "id": moment_id,
            "source": source,
            "started_at": started_at,
            "duration_seconds": duration,
            "lat": lat,
            "lon": lon,
            "place": {"id": place_id, "name": name} if place_id else None,
        }
        for moment_id, source, started_at, duration, lat, lon, place_id, name in rows
    ]


def list_places(conn: psycopg.Connection) -> list[dict]:
    """Return every place, most recently updated first, with its rooms and how many moments were there."""
    rows = conn.execute(
        "select p.id, p.name, p.lat, p.lon, p.updated_at, p.map_path is not null,"
        "       (select count(distinct s.moment_id) from segment s where s.place_id = p.id),"
        "       (select coalesce(json_agg(r.name order by r.name), '[]') from room r where r.place_id = p.id)"
        "  from place p order by p.updated_at desc"
    )

    return [
        {
            "id": place_id,
            "name": name,
            "lat": lat,
            "lon": lon,
            "updated_at": updated_at,
            "has_map": has_map,
            "moments": moments,
            "rooms": rooms,
        }
        for place_id, name, lat, lon, updated_at, has_map, moments, rooms in rows
    ]


def place_map(conn: psycopg.Connection, place_id: int) -> str | None:
    """Return the path of a place's saved ARKit map."""
    row = conn.execute("select map_path from place where id = %s", (place_id,)).fetchone()

    return row[0] if row else None


def save_place(conn: psycopg.Connection, place_id: int | None, map_path: str | None, lat, lon) -> int:
    """Create a place, or give an existing one a newer map. Return its id."""
    if place_id is not None:
        conn.execute(
            "update place set map_path = coalesce(%s, map_path), updated_at = now() where id = %s", (map_path, place_id)
        )
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
    return (
        conn.execute(
            "update place set name = %s, named = named or %s where id = %s and (%s or not named)",
            (name, by_user, place_id, by_user),
        ).rowcount
        > 0
    )


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
        cursor.executemany(
            "update frame set room_id = %s where id = %s", [(room, frame) for frame, room in rooms.items()]
        )


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
    conn.execute(
        "delete from place p where not p.named and not exists (select 1 from segment s where s.place_id = p.id)"
    )


def name_room(conn: psycopg.Connection, room_id: int, name: str) -> int | None:
    """Rename a room as the user asked. Return its place's id, or None if there is no such room."""
    row = conn.execute(
        "update room set name = %s, named = true where id = %s returning place_id", (name, room_id)
    ).fetchone()

    return row[0] if row else None


def forget_unnamed_places(conn: psycopg.Connection) -> None:
    """Clear every segment and room label, and delete the places and rooms nobody named."""
    conn.execute("delete from segment")
    conn.execute("update frame set room_id = null")
    conn.execute("delete from room where not named")
    conn.execute("delete from place where not named")
