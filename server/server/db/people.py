"""People and the faces seen of them."""

import psycopg


def frame_images(conn: psycopg.Connection, moment_id: str) -> list[tuple]:
    """Return (frame id, image path) for every frame of a moment, oldest first."""
    return conn.execute(
        "select id, image_path from frame where moment_id = %s order by captured_at", (moment_id,)
    ).fetchall()


def clear_faces(conn: psycopg.Connection, moment_id: str) -> None:
    """Delete the faces found in a moment's frames."""
    conn.execute("delete from face where frame_id in (select id from frame where moment_id = %s)", (moment_id,))


def person_embedding_sums(conn: psycopg.Connection) -> dict:
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


def list_people(conn: psycopg.Connection, person_id: int | None = None) -> list[dict]:
    """Return every person, or just one, with a face count, when they were seen, and their clearest face."""
    rows = conn.execute(
        "select p.id, p.name, count(f.id), min(fr.captured_at), max(fr.captured_at),"
        "       (select f2.id from face f2 where f2.person_id = p.id order by f2.w * f2.h * f2.score desc limit 1)"
        "  from person p join face f on f.person_id = p.id join frame fr on fr.id = f.frame_id"
        " where %(id)s::bigint is null or p.id = %(id)s::bigint"
        " group by p.id order by count(f.id) desc",
        {"id": person_id},
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


def person_name(conn: psycopg.Connection, person_id: int) -> tuple[bool, str | None]:
    """Return whether a person exists and their name, if they have one."""
    row = conn.execute("select name from person where id = %s", (person_id,)).fetchone()

    return (row is not None, row[0] if row else None)


def name_person(conn: psycopg.Connection, person_id: int, name: str | None) -> bool:
    """Set or clear a person's name. Return whether they exist."""
    return conn.execute("update person set name = %s where id = %s", (name, person_id)).rowcount > 0


def merge_people(conn: psycopg.Connection, person_id: int, into: int) -> None:
    """Move every face of one person to another, then delete the first."""
    with conn.transaction():
        conn.execute("update face set person_id = %s where person_id = %s", (into, person_id))
        conn.execute(
            "update person set name = coalesce(name, (select name from person where id = %s)) where id = %s",
            (person_id, into),
        )
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


def person_photos(conn: psycopg.Connection, person_id: int) -> list[dict]:
    """Return the clearest photo of a person from each scene they are in, newest first."""
    rows = conn.execute(
        "select distinct on (coalesce(s.episode_id::text, fr.moment_id)) fr.id, fr.image_path, fr.captured_at,"
        "       fr.moment_id, extract(epoch from fr.captured_at - m.started_at)"
        "  from face f join frame fr on fr.id = f.frame_id join moment m on m.id = fr.moment_id"
        "  left join sighting s on s.frame_id = fr.id"
        "   and s.entity_id = (select id from entity where person_id = %(person)s)"
        " where f.person_id = %(person)s"
        " order by coalesce(s.episode_id::text, fr.moment_id), f.w * f.h * f.score desc",
        {"person": person_id},
    )
    photos = [
        {"id": frame_id, "image_path": path, "at": at, "moment_id": moment_id, "offset_seconds": float(offset)}
        for frame_id, path, at, moment_id, offset in rows
    ]

    return sorted(photos, key=lambda photo: photo["at"], reverse=True)


def person_recordings(conn: psycopg.Connection, person_id: int) -> list[dict]:
    """Return the moments a person is in, newest first, with title, start and length."""
    rows = conn.execute(
        "select m.id, m.title, m.started_at, m.duration_seconds from moment m"
        " where exists (select 1 from face f join frame fr on fr.id = f.frame_id"
        "               where f.person_id = %s and fr.moment_id = m.id)"
        " order by m.started_at desc",
        (person_id,),
    )

    return [
        {"id": moment_id, "title": title, "started_at": started_at, "duration_seconds": duration}
        for moment_id, title, started_at, duration in rows
    ]
