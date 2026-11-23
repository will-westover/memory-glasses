"""Rename people and rooms in the facts already written, without asking a model to write them again."""

import re

import psycopg

from server import llm


def replaced(text: str | None, old: str, new: str) -> str | None:
    """Return the text with every whole-word use of an old name swapped for a new one."""
    if text is None:
        return None

    return re.sub(rf"(?<!\w){re.escape(old)}(?!\w)", lambda _: new, text)


def rename_in_facts(conn: psycopg.Connection, facts: list[tuple], old: str, new: str) -> int:
    """Swap a name in the given (id, text, title) facts, embed the changed ones again, and return how many changed."""
    changed = [
        (fact_id, replaced(text, old, new), replaced(title, old, new))
        for fact_id, text, title in facts
        if replaced(text, old, new) != text or replaced(title, old, new) != title
    ]

    if not changed:
        return 0

    vectors = llm.embed(conn, [text for _, text, _ in changed], "embed-rename")

    with conn.cursor() as cursor:
        cursor.executemany(
            "update fact set text = %s, title = %s, embedding = %s::halfvec where id = %s",
            [(text, title, vector, fact_id) for (fact_id, text, title), vector in zip(changed, vectors)],
        )

    return len(changed)


def rename_person(conn: psycopg.Connection, moment_ids: list[str], person_id: int, old: str, new: str) -> int:
    """Swap a person's old name for the new one in the facts of the given moments."""
    facts = conn.execute("select id, text, title from fact where moment_id = any(%s)", (moment_ids,)).fetchall()
    conn.execute("update entity set name = %s where person_id = %s", (new, person_id))
    rename_in_scenes(conn, moment_ids, old, new)
    rename_things(conn, moment_ids, old, new)

    return rename_in_facts(conn, facts, old, new)


def rename_in_scenes(conn: psycopg.Connection, moment_ids: list[str], old: str, new: str) -> None:
    """Swap a name in the titles and summaries of the scenes, activities and days of the given moments."""
    rows = conn.execute(
        "select id, title, summary from episode where moment_id = any(%(ids)s) or (level = 'day' and day in"
        " (select (started_at at time zone current_setting('TimeZone'))::date from moment where id = any(%(ids)s)))",
        {"ids": moment_ids},
    ).fetchall()

    with conn.cursor() as cursor:
        cursor.executemany(
            "update episode set title = %s, summary = %s where id = %s",
            [(replaced(title, old, new), replaced(summary, old, new), episode_id) for episode_id, title, summary in rows],
        )


def rename_things(conn: psycopg.Connection, moment_ids: list[str], old: str, new: str) -> None:
    """Swap a name in the things seen in the given moments, like a top worn by someone, and embed them again."""
    rows = conn.execute(
        "select distinct e.id, e.name, e.details from entity e join sighting s on s.entity_id = e.id"
        " join frame f on f.id = s.frame_id where e.kind = 'object' and f.moment_id = any(%s)",
        (moment_ids,),
    ).fetchall()
    changed = [
        (entity_id, replaced(name, old, new), replaced(details, old, new))
        for entity_id, name, details in rows
        if replaced(name, old, new) != name or replaced(details, old, new) != details
    ]

    if not changed:
        return

    vectors = llm.embed(conn, [f"{name}: {details or ''}" for _, name, details in changed], "embed-rename")

    with conn.cursor() as cursor:
        cursor.executemany(
            "update entity set name = %s, details = %s, embedding = %s::halfvec where id = %s",
            [(name, details, vector, entity_id) for (entity_id, name, details), vector in zip(changed, vectors)],
        )


def move_person_entity(conn: psycopg.Connection, person_id: int, into: int) -> None:
    """Make every sighting and fact of one person's entity belong to the person they are being merged into."""
    entities = dict(conn.execute("select person_id, id from entity where person_id = any(%s)", ([person_id, into],)))
    moving, staying = entities.get(person_id), entities.get(into)

    if moving and staying:
        conn.execute("update sighting set entity_id = %s where entity_id = %s", (staying, moving))
        conn.execute("update fact set entity_id = %s where entity_id = %s", (staying, moving))
        conn.execute("delete from entity where id = %s", (moving,))
    elif moving:
        conn.execute("update entity set person_id = %s where id = %s", (into, moving))


def rename_room(conn: psycopg.Connection, room_id: int, old: str, new: str) -> int:
    """Swap a room's old name for the new one in the facts made from photos taken in it."""
    facts = conn.execute(
        "select distinct f.id, f.text, f.title from fact f join fact_frame ff on ff.fact_id = f.id"
        " join frame fr on fr.id = ff.frame_id where fr.room_id = %s",
        (room_id,),
    ).fetchall()

    return rename_in_facts(conn, facts, f"Room: {old}", f"Room: {new}")


def rename_place(conn: psycopg.Connection, moment_ids: list[str], old: str, new: str) -> int:
    """Swap a place's old name for the new one in the facts of the moments recorded there."""
    facts = conn.execute("select id, text, title from fact where moment_id = any(%s)", (moment_ids,)).fetchall()

    return rename_in_facts(conn, facts, f"At {old}", f"At {new}")
