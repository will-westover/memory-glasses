"""Conversations: asking, streaming answers, suggestions and search."""

import json
from datetime import datetime
from uuid import UUID

import psycopg
from fastapi.encoders import jsonable_encoder
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse

from server import db
from server.agent import chat
from server.agent import answer_events, ask_agent, fact_record
from server.agent.search import search
from server.api.shared import ChatQuestion, connection, fact_json, image_url

router = APIRouter()


@router.get("/search")
def search_memories(
    q: str = Query(min_length=1), limit: int = Query(40, ge=1, le=100), conn: psycopg.Connection = Depends(connection)
) -> dict:
    """Return the facts that best match some words, best first, for search inside the Timeline."""
    facts = search(conn, q, limit=limit)
    details = db.source_details(conn, facts)

    return {"results": [fact_json(fact, details.get(fact["id"])) for fact in facts]}


def turn_json(turn: dict, details: dict) -> dict:
    """Return a stored turn in the shape the app reads."""
    answer = None

    if turn["answer"] is not None:
        answer = {
            "answer": turn["answer"],
            "found": turn["found"],
            "sources": [fact_json(fact, details.get(fact["id"])) for fact in turn["sources"]],
            "model_calls": turn["model_calls"],
            "seconds": turn["seconds"],
            "usd": turn["usd"],
            "highlight": turn["highlight"],
            "highlight_reason": turn["highlight_reason"],
            "layout": turn["layout"],
            "follow_ups": turn["follow_ups"],
            "next_steps": turn["next_steps"],
        }

    return {
        "id": turn["id"],
        "asked_at": turn["asked_at"],
        "question": turn["question"],
        "answer": answer,
        "problem": turn["problem"],
    }


def turns_json(conn: psycopg.Connection, turns: list[dict]) -> list[dict]:
    """Return turns in the shape the app reads, with who and where for every source."""
    details = db.source_details(conn, [fact for turn in turns for fact in turn["sources"]])

    return [turn_json(turn, details) for turn in turns]


@router.get("/conversations")
def conversations(q: str | None = None, conn: psycopg.Connection = Depends(connection)) -> list[dict]:
    """List conversations, newest first; only those whose title or messages mention `q` if given."""
    return db.conversations(conn, q.strip() if q and q.strip() else None)


@router.post("/conversations")
def start_conversation(conn: psycopg.Connection = Depends(connection)) -> dict:
    """Start an empty conversation."""
    return {"id": db.new_conversation(conn), "turns": []}


@router.get("/conversations/{conversation_id}")
def conversation(conversation_id: UUID, conn: psycopg.Connection = Depends(connection)) -> dict:
    """Return one conversation with every question, answer and source."""
    turns = db.conversation_turns(conn, conversation_id)

    if turns is None:
        raise HTTPException(404, "no such conversation")

    return {"id": conversation_id, "turns": turns_json(conn, turns)}


def question_context(
    conn: psycopg.Connection, conversation_id: UUID, body: ChatQuestion
) -> tuple[list, dict | None, dict]:
    """Return the conversation so far, the frame asked about, and the question as a message to store."""
    turns = db.conversation_turns(conn, conversation_id)

    if turns is None:
        raise HTTPException(404, "no such conversation")

    frame = db.frame_fact(conn, body.frame_id) if body.frame_id else None

    if body.frame_id and frame is None:
        raise HTTPException(404, "no such frame")

    history = [(turn["question"], turn["answer"]) for turn in turns if turn["answer"]]
    content = {"text": body.question}

    if frame:
        content |= {"frame_id": body.frame_id, "facts": [fact_record(frame)]}

    return history, frame, {"role": "user", "at": datetime.now().astimezone(), "content": content}


def failure_messages(error: Exception) -> list[dict]:
    """Return the message stored when an answer fails."""
    return [
        {"role": "assistant", "at": datetime.now().astimezone(), "content": {"error": f"The answer failed: {error}"}}
    ]


def save_turn(conn: psycopg.Connection, conversation_id: UUID, messages: list[dict]) -> dict:
    """Store a question and what followed it, and return that turn as the app reads it."""
    turn_id = db.save_messages(conn, conversation_id, messages)
    turn = next(turn for turn in db.conversation_turns(conn, conversation_id) if turn["id"] == turn_id)

    return turns_json(conn, [turn])[0]


def give_title(conversation_id: UUID, question: str, answer: str | None) -> None:
    """Name a conversation from its first question and answer, if it has no title yet."""
    with db.pool().connection() as conn:
        if db.conversation_title(conn, conversation_id) is None:
            db.set_title(conn, conversation_id, chat.make_title(conn, question, answer or "(no answer)"))


@router.post("/conversations/{conversation_id}/ask")
def ask_in_conversation(
    conversation_id: UUID, body: ChatQuestion, conn: psycopg.Connection = Depends(connection)
) -> dict:
    """Answer a question using the conversation so far, and store both."""
    history, frame, question_message = question_context(conn, conversation_id, body)

    try:
        replies = ask_agent(conn, body.question, history=history, frame=frame)["messages"]
    except Exception as error:
        conn.rollback()
        replies = failure_messages(error)

    turn = save_turn(conn, conversation_id, [question_message, *replies])
    conn.commit()
    give_title(conversation_id, body.question, (turn["answer"] or {}).get("answer"))

    return turn


def sse_event(name: str, data: dict) -> str:
    """Return one server-sent event."""
    return f"event: {name}\ndata: {json.dumps(data, default=str)}\n\n"


def answer_stream(conversation_id: UUID, body: ChatQuestion, history: list, frame: dict | None, question_message: dict):
    """Yield each step and piece of the answer as server-sent events, then store the turn and send it."""
    with db.pool().connection() as conn:
        replies = None

        try:
            for kind, payload in answer_events(conn, body.question, history=history, frame=frame):
                if kind == "result":
                    replies = payload["messages"]
                elif kind == "step":
                    images = [image_url(path) for path in payload["image_paths"]]
                    yield sse_event("step", {"kind": payload["kind"], "text": payload["text"], "images": images})
                else:
                    yield sse_event(kind, payload)
        except Exception as error:
            conn.rollback()
            replies = failure_messages(error)
            yield sse_event("error", {"message": f"The answer failed: {error}"})

        turn = save_turn(conn, conversation_id, [question_message, *replies])
        conn.commit()

        yield sse_event("done", jsonable_encoder(turn))

    give_title(conversation_id, body.question, (turn["answer"] or {}).get("answer"))


@router.post("/conversations/{conversation_id}/ask/stream")
def ask_stream(
    conversation_id: UUID, body: ChatQuestion, conn: psycopg.Connection = Depends(connection)
) -> StreamingResponse:
    """Answer a question as server-sent events: each step, the answer as it is written, then the stored turn."""
    history, frame, question_message = question_context(conn, conversation_id, body)

    return StreamingResponse(
        answer_stream(conversation_id, body, history, frame, question_message),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache"},
    )


SUGGESTIONS_CACHE: dict = {"moment": None, "questions": []}


@router.get("/suggestions")
def suggestions(conn: psycopg.Connection = Depends(connection)) -> dict:
    """Return questions to suggest, written from the newest records and kept until a new moment arrives."""
    latest = db.latest_moment_id(conn)

    if latest != SUGGESTIONS_CACHE["moment"] or not SUGGESTIONS_CACHE["questions"]:
        SUGGESTIONS_CACHE.update(moment=latest, questions=chat.suggest(conn))

    return {"questions": SUGGESTIONS_CACHE["questions"]}


@router.delete("/conversations/{conversation_id}")
def delete_conversation(conversation_id: UUID, conn: psycopg.Connection = Depends(connection)) -> dict:
    """Delete a conversation."""
    if not db.delete_conversation(conn, conversation_id):
        raise HTTPException(404, "no such conversation")

    return {"deleted": str(conversation_id)}
