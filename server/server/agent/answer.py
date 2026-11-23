"""The answer the model gives, and the result handed back with its checked sources."""

import time
from typing import Literal

from pydantic import BaseModel, Field

from server.citations import renumber


class NextStep(BaseModel):
    label: str = Field(description="A few words, e.g. Search all of last week")
    question: str = Field(description="The full question to ask instead")


class Answer(BaseModel):
    answer: str = Field(
        description="The answer in a sentence or two of plain words, with [fact number] after each claim"
    )
    found: bool = Field(description="True if the facts answer the question, false if they do not")
    sources: list[int] = Field(description="Numbers of the facts the answer rests on, in the order first used")
    highlight: int | None = Field(description="Number of the fact that best shows the answer, or null")
    highlight_reason: str | None = Field(description="Why that fact, in a few words, or null")
    layout: Literal["photo", "quote", "people", "place", "timeline", "text"]
    follow_ups: list[str] = Field(description="Two or three short questions the wearer might ask next")
    next_steps: list[NextStep] = Field(description="Other ways to look when found is false; empty otherwise")


NO_ANSWER = Answer(
    answer="The model gave no answer.",
    found=False,
    sources=[],
    highlight=None,
    highlight_reason=None,
    layout="text",
    follow_ups=[],
    next_steps=[],
)


def answer_result(answer: Answer | None, seen: dict, calls: list, usd: float, model_calls: int, started: float) -> dict:
    """Bundle an answer with its checked sources and what it took to get."""
    answer = answer or NO_ANSWER
    text, kept = renumber(answer.answer, answer.sources, seen)

    return {
        "answer": text,
        "found": answer.found,
        "sources": [seen[fact_id] for fact_id in kept],
        "highlight": kept.index(answer.highlight) if answer.highlight in kept else None,
        "highlight_reason": answer.highlight_reason if answer.highlight in kept else None,
        "layout": answer.layout,
        "follow_ups": answer.follow_ups,
        "next_steps": [step.model_dump() for step in answer.next_steps] if not answer.found else [],
        "shown": list(seen.values()),
        "calls": calls,
        "model_calls": model_calls,
        "usd": usd,
        "seconds": time.monotonic() - started,
    }
