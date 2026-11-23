"""The baseline the evals compare the agent with: one search and one model call, no tools."""

import time

from server.shared import llm
from server.agent.answer import Answer, answer_result
from server.agent.prompt import PLAIN_NOTES, instructions
from server.agent.reading import fact_lines
from server.shared.config import AGENT_MODEL
from server.agent.search import search_within
from server.agent.when import question_window

PLAIN_RESULTS = 10


def ask_plain(conn, question: str, model: str = AGENT_MODEL, moments: list[str] | None = None) -> dict:
    """Answer a question from one search and one model call."""
    llm.require_price(model)
    started = time.monotonic()

    window, when_usd = question_window(conn, question)
    facts = search_within(conn, question, PLAIN_RESULTS, window, moments)

    response = llm.client().responses.parse(
        model=model,
        instructions=instructions(conn, PLAIN_NOTES, moments),
        input=f"Question: {question}\n\nFacts:\n{fact_lines(facts)}",
        text_format=Answer,
        reasoning={"effort": "low"},
    )
    usd = llm.log_cost(conn, "rag", model, response.usage)
    seen = {fact["id"]: fact for fact in facts}

    return answer_result(response.output_parsed, seen, [], usd + when_usd, 2, started)
