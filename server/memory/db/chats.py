"""Conversations, their messages, and what model calls cost."""

from datetime import datetime

import psycopg
from psycopg.types.json import Jsonb

from memory.citations import renumber


def step_cost(conn: psycopg.Connection, step: str) -> float:
    """Return the total dollars logged for one pipeline step."""
    (usd,) = conn.execute("select coalesce(sum(usd), 0) from model_call where step = %s", (step,)).fetchone()

    return float(usd)


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


def caption_cost(conn: psycopg.Connection) -> float:
    """Return the average cost of describing one frame so far."""
    (average,) = conn.execute("select coalesce(avg(usd), 0.006) from model_call where step = 'caption'").fetchone()

    return float(average)
