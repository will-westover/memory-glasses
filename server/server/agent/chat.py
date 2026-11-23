"""Short model calls around a chat: a title for each conversation and questions to suggest."""

from datetime import datetime

from pydantic import BaseModel, Field

from server import db
from server.shared import llm
from server.agent import fact_line
from server.shared.config import SUMMARY_MODEL

TITLE_INSTRUCTIONS = """\
Write a title of two to five words for a conversation that starts with this question and answer, the way a chat app
names a thread. No quotation marks and no full stop."""

SUGGEST_INSTRUCTIONS = """\
Someone wears camera glasses that record their day. Below is an overview of what was recorded most recently. Write
three very simple questions they might ask to remember it, like "What did I do this morning?", "What did we talk
about at dinner?" or "Who was I with on Friday?". Each under seven words, in plain everyday words, as if they had
forgotten. Never put the answer or a detail of it in the question. Use times the way people say them (today,
yesterday, this morning, Friday), counted from now, never clock times or dates. Make the three about different
things: what they did, what was said, and who they were with or where.

It is now {now}."""


class Title(BaseModel):
    title: str = Field(description="Two to five words")


class Suggestions(BaseModel):
    questions: list[str] = Field(description="Three very simple questions")


def make_title(conn, question: str, answer: str) -> str:
    """Ask a model for a conversation's title."""
    llm.require_price(SUMMARY_MODEL)
    request = {
        "model": SUMMARY_MODEL,
        "instructions": TITLE_INSTRUCTIONS,
        "input": f"Question: {question}\n\nAnswer: {answer}",
    }

    response = llm.client().responses.parse(**request, text_format=Title, reasoning={"effort": "low"})
    llm.log_cost(conn, "title", SUMMARY_MODEL, response.usage)

    return response.output_parsed.title.strip().strip('".') if response.output_parsed else question[:40]


def suggest(conn) -> list[str]:
    """Ask a model for questions to suggest, written from the newest records."""
    facts = db.recent_facts(conn)

    if not facts:
        return []

    llm.require_price(SUMMARY_MODEL)
    request = {
        "model": SUMMARY_MODEL,
        "instructions": SUGGEST_INSTRUCTIONS.format(now=f"{datetime.now().astimezone():%A %d %B %Y, %H:%M}"),
        "input": "\n".join(fact_line(fact) for fact in reversed(facts)),
    }

    response = llm.client().responses.parse(**request, text_format=Suggestions, reasoning={"effort": "low"})
    llm.log_cost(conn, "suggest", SUMMARY_MODEL, response.usage)
    conn.commit()

    return response.output_parsed.questions[:3] if response.output_parsed else []
