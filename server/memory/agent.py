"""Answer a question from what the glasses recorded."""

import argparse
import json
import math
import sys
import time
from datetime import datetime, timedelta

from pydantic import BaseModel, Field

from memory import db, llm
from memory.config import AGENT_MODEL, clock
from memory.places import place_for
from memory.search import search

MAX_ROUNDS = 5

SEARCH_RESULTS = 8
PLAIN_RESULTS = 10

TIMELINE_LIMIT = 60

RECENT_SESSIONS = 20

INSTRUCTIONS = """\
You answer questions for someone who wears camera glasses. The glasses record what the wearer sees and hears,
and that record is kept as facts: moments (what was happening), objects and where each one was, text that was
readable, speech, and summaries of what happened and what was said. Each fact starts with its number in [brackets] and the time it was recorded. "I" and "me"
in a question mean the wearer. Speakers are labelled with letters, not names.

Answer only from the facts. Do not guess or add outside knowledge. A search always returns its closest matches,
even when nothing relevant was recorded, so check that a fact really answers the question before relying on it.
If the facts do not answer the question, set found to false, say the record does not show it, and leave sources
empty. Do not pad the answer with other things that were recorded.

Keep the answer short and direct. If facts differ because something changed over time, say what the latest one
shows and when. In sources, give the numbers of the facts the answer rests on.

It is now {now}. Times without a UTC offset are read as this local time.
Recordings:
{recordings}"""

AGENT_NOTES = """

You have two tools. search finds facts by what they are about. timeline lists what happened between two times,
in order: use it for questions about a stretch of time, or about what came before or after something. Call them
until you can answer. If a search finds nothing relevant, try other words before giving up."""

PLAIN_NOTES = """

You are given the facts that one search found for the question. You cannot search again."""

LONG_STRETCH = "This stretch is long, so it is summarised. Ask for a shorter stretch to see every moment."

ISO_TIME = "ISO 8601, e.g. 2026-11-11T19:54:00"

TOOLS = [
    {
        "type": "function",
        "name": "search",
        "description": "Find facts by what they are about. Returns the best matches of every kind.",
        "strict": True,
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "What to look for, in a few plain words"},
                "start": {"type": ["string", "null"], "description": f"Only facts at or after this time. {ISO_TIME}"},
                "end": {"type": ["string", "null"], "description": f"Only facts at or before this time. {ISO_TIME}"},
            },
            "required": ["query", "start", "end"],
            "additionalProperties": False,
        },
    },
    {
        "type": "function",
        "name": "timeline",
        "description": "List what happened between two times, in order: each moment and what was said.",
        "strict": True,
        "parameters": {
            "type": "object",
            "properties": {
                "start": {"type": "string", "description": f"Start of the stretch. {ISO_TIME}"},
                "end": {"type": "string", "description": f"End of the stretch. {ISO_TIME}"},
            },
            "required": ["start", "end"],
            "additionalProperties": False,
        },
    },
]


class Answer(BaseModel):
    answer: str = Field(description="The answer, in a sentence or two of plain words")
    found: bool = Field(description="True if the facts answer the question, false if they do not")
    sources: list[int] = Field(description="Numbers of the facts the answer rests on")


def instructions(conn, notes: str) -> str:
    """Return the instructions with the current time and the newest recordings filled in."""
    places = db.places(conn)
    lines = []

    for _, _, started_at, duration, _, _, lat, lon in db.list_sessions(conn)[:RECENT_SESSIONS]:
        place = place_for(lat, lon, places)

        start = started_at.astimezone()
        end = start + timedelta(seconds=duration)

        lines.append(f"- {start:%a %d %b %Y %H:%M:%S} to {end:%H:%M:%S}" + (f", at {place}" if place else ""))

    now = datetime.now().astimezone()

    return INSTRUCTIONS.format(now=f"{now:%A %d %B %Y %H:%M} (UTC{now:%z})", recordings="\n".join(lines)) + notes


def parse_time(text: str | None) -> datetime | None:
    """Read an ISO 8601 time. One without a UTC offset is taken as local time."""
    if not text:
        return None

    at = datetime.fromisoformat(text)

    return at if at.tzinfo else at.astimezone()


def fit(facts: list[dict], limit: int = TIMELINE_LIMIT) -> list[dict]:
    """Return the moments and speech if they fit in `limit`, otherwise the summaries of that stretch."""
    detail = [fact for fact in facts if fact["kind"] != "summary"]

    if len(detail) <= limit:
        return detail

    overview = [fact for fact in facts if fact["kind"] == "summary"] or detail

    return overview[:: math.ceil(len(overview) / limit)]


def fact_line(fact: dict) -> str:
    """Return a fact as the one line the model reads."""
    return f"[{fact['id']}] {fact['at'].astimezone():%a %d %b %Y %H:%M:%S}  {fact['kind']}: {fact['text']}"


def fact_lines(facts: list[dict]) -> str:
    """Return facts as the text the model reads."""
    if not facts:
        return "Nothing was recorded that matches."

    return "\n".join(fact_line(fact) for fact in facts)


def find_facts(conn, name: str, arguments: dict) -> list[dict]:
    """Return the facts one tool call asks for."""
    start = parse_time(arguments.get("start"))
    end = parse_time(arguments.get("end"))

    if name == "search":
        return search(conn, arguments["query"], SEARCH_RESULTS, start, end)

    if not start or not end:
        raise ValueError("timeline needs a start and an end")

    return fit(db.timeline(conn, start, end))


def run_tool(conn, name: str, arguments: dict, seen: dict) -> str:
    """Run one tool call, remember the facts it returned, and return them as text."""
    try:
        facts = find_facts(conn, name, arguments)
    except ValueError:
        return f"error: start and end must be {ISO_TIME}"

    seen.update({fact["id"]: fact for fact in facts})

    if name == "timeline" and facts and facts[0]["kind"] == "summary":
        return f"{LONG_STRETCH}\n{fact_lines(facts)}"

    return fact_lines(facts)


def check_sources(cited: list[int], seen: dict) -> list[dict]:
    """Return the cited facts, once each, leaving out any the model was never shown."""
    return [seen[fact_id] for fact_id in dict.fromkeys(cited) if fact_id in seen]


def ask_agent(conn, question: str, model: str = AGENT_MODEL) -> dict:
    """Answer a question by letting the model call the tools."""
    llm.require_price(model)
    started = time.monotonic()
    prompt = instructions(conn, AGENT_NOTES)

    seen: dict = {}
    calls = []
    usd = 0.0

    send = [{"role": "user", "content": question}]
    previous_id = None

    for round_number in range(MAX_ROUNDS + 1):
        response = llm.client().responses.parse(
            model=model,
            instructions=prompt,
            input=send,
            previous_response_id=previous_id,
            tools=TOOLS,
            tool_choice="auto" if round_number < MAX_ROUNDS else "none",
            text_format=Answer,
            reasoning={"effort": "low"},
        )
        usd += llm.log_cost(conn, "agent", model, response.usage)

        requests = [item for item in response.output if item.type == "function_call"]

        if not requests:
            break

        send = []
        previous_id = response.id

        for request in requests:
            arguments = json.loads(request.arguments)
            calls.append((request.name, arguments))

            result = run_tool(conn, request.name, arguments, seen)
            send.append({"type": "function_call_output", "call_id": request.call_id, "output": result})

    return finish(response.output_parsed, seen, calls, usd, round_number + 1, started)


def ask_plain(conn, question: str, model: str = AGENT_MODEL) -> dict:
    """Answer a question from one search and one model call."""
    llm.require_price(model)
    started = time.monotonic()

    facts = search(conn, question, PLAIN_RESULTS)
    seen = {fact["id"]: fact for fact in facts}

    response = llm.client().responses.parse(
        model=model,
        instructions=instructions(conn, PLAIN_NOTES),
        input=f"Question: {question}\n\nFacts:\n{fact_lines(facts)}",
        text_format=Answer,
        reasoning={"effort": "low"},
    )
    usd = llm.log_cost(conn, "rag", model, response.usage)

    return finish(response.output_parsed, seen, [], usd, 1, started)


def finish(answer: Answer | None, seen: dict, calls: list, usd: float, model_calls: int, started: float) -> dict:
    """Bundle an answer with its checked sources and what it took to get."""
    answer = answer or Answer(answer="The model gave no answer.", found=False, sources=[])

    return {
        "answer": answer.answer,
        "found": answer.found,
        "sources": check_sources(answer.sources, seen),
        "calls": calls,
        "model_calls": model_calls,
        "usd": usd,
        "seconds": time.monotonic() - started,
    }


def print_sources(sources: list[dict]) -> None:
    if sources:
        print("\nSources:")

    for fact in sources:
        print(f"  [{fact['id']}] {fact['at'].astimezone():%a %d %b %H:%M:%S}, {clock(fact['offset_seconds'])} into"
              f" {fact['session_id']}\n      {fact['text']}")

        if fact["image_path"]:
            print(f"      {fact['image_path']}")


def print_calls(calls: list[tuple]) -> None:
    if calls:
        print("\nTool calls:")

    for name, arguments in calls:
        given = ", ".join(f"{key}={value!r}" for key, value in arguments.items() if value is not None)
        print(f"  {name}({given})")


def print_result(result: dict) -> None:
    print(result["answer"] if result["found"] else f"(not found) {result['answer']}")

    print_sources(result["sources"])
    print_calls(result["calls"])

    print(f"\n{result['model_calls']} model calls, {result['seconds']:.1f} s, ${result['usd']:.5f}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Answer a question from what the glasses recorded.")
    parser.add_argument("question")
    parser.add_argument("--plain", action="store_true", help="one search and one model call, with no tools")
    parser.add_argument("--model", default=AGENT_MODEL)
    args = parser.parse_args()

    with db.connect() as conn:
        result = (ask_plain if args.plain else ask_agent)(conn, args.question, args.model)

    print_result(result)

    return 0


if __name__ == "__main__":
    sys.exit(main())
