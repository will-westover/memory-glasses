"""Short model calls around a chat: a title for each conversation and questions to suggest."""

from pydantic import BaseModel, Field

from memory import db, llm
from memory.agent import fact_line
from memory.config import SUMMARY_MODEL

TITLE_INSTRUCTIONS = """\
Write a title of two to five words for a conversation that starts with this question and answer, the way a chat app
names a thread. No quotation marks and no full stop."""

SUGGEST_INSTRUCTIONS = """\
Someone wears camera glasses that record what they see and hear. Below is the newest of what was recorded. Write
three or four short questions they could ask about it, in their own voice, each answerable from these records.
Vary them across what was seen, what was said, who was there, where they were and when things happened. Keep each
under ten words, and make them specific to what is here."""


class Title(BaseModel):
    title: str = Field(description="Two to five words")


class Suggestions(BaseModel):
    questions: list[str] = Field(description="Three or four short questions")


def make_title(conn, question: str, answer: str) -> str:
    """Ask a model for a conversation's title."""
    llm.require_price(SUMMARY_MODEL)
    request = {"model": SUMMARY_MODEL, "instructions": TITLE_INSTRUCTIONS,
               "input": f"Question: {question}\n\nAnswer: {answer}"}

    response = llm.client().responses.parse(**request, text_format=Title, reasoning={"effort": "low"})
    llm.log_cost(conn, "title", SUMMARY_MODEL, response.usage)

    return response.output_parsed.title.strip().strip('".') if response.output_parsed else question[:40]


def suggest(conn) -> list[str]:
    """Ask a model for questions to suggest, written from the newest records."""
    facts = db.recent_facts(conn)

    if not facts:
        return []

    llm.require_price(SUMMARY_MODEL)
    request = {"model": SUMMARY_MODEL, "instructions": SUGGEST_INSTRUCTIONS,
               "input": "\n".join(fact_line(fact) for fact in reversed(facts))}

    response = llm.client().responses.parse(**request, text_format=Suggestions, reasoning={"effort": "low"})
    llm.log_cost(conn, "suggest", SUMMARY_MODEL, response.usage)
    conn.commit()

    return response.output_parsed.questions[:4] if response.output_parsed else []
