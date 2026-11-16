"""Answer a question from what the glasses recorded.

    uv run python -m memory.agent "what was I doing in the kitchen?"
    uv run python -m memory.agent "what was I doing in the kitchen?" --plain

There are two ways to answer, and both give an answer, whether it was found, and the facts it rests on:
  agent   the model calls tools as often as it needs, then answers
  plain   one search on the question, then one model call (plain RAG, the baseline to compare against)

The agent has two tools:
  search     finds facts by what they are about, optionally within a stretch of time
  timeline   lists what happened between two times, in order
"""

import argparse
import json
import math
import sys
import time
from datetime import datetime, timedelta

from pydantic import BaseModel, Field

from memory import db, llm
from memory.config import AGENT_MODEL
from memory.places import place_for
from memory.search import clock, search

# The agent may ask for tools this many times. After that it has to answer with what it has.
MAX_ROUNDS = 5

# How many facts one search hands the agent, and how many the plain mode gets from its single search.
SEARCH_RESULTS = 8
PLAIN_RESULTS = 10

# The most facts one timeline call returns. Longer stretches are thinned out to fit.
TIMELINE_LIMIT = 60

# How many of the newest recordings are listed in the instructions.
RECENT_SESSIONS = 20

INSTRUCTIONS = """\
You answer questions for someone who wears camera glasses. The glasses record what the wearer sees and hears,
and that record is kept as facts: moments (what was happening), objects and where each one was, text that was
readable, and speech. Each fact starts with its number in [brackets] and the time it was recorded. "I" and "me"
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

    for session_id, _, started_at, duration, _, _ in db.list_sessions(conn)[:RECENT_SESSIONS]:
        _, lat, lon = db.session_info(conn, session_id)
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


def thin(facts: list[dict], limit: int = TIMELINE_LIMIT) -> list[dict]:
    """Cut a long timeline down to about `limit` facts by keeping evenly spaced moments and all the speech."""
    if len(facts) <= limit:
        return facts

    moments = [fact for fact in facts if fact["kind"] == "moment"]
    room = max(limit - (len(facts) - len(moments)), 1)
    keep = {fact["id"] for fact in moments[:: math.ceil(len(moments) / room)]}

    return [fact for fact in facts if fact["kind"] != "moment" or fact["id"] in keep]


def fact_line(fact: dict) -> str:
    """Return a fact as the one line the model reads."""
    return f"[{fact['id']}] {fact['at'].astimezone():%a %d %b %Y %H:%M:%S}  {fact['kind']}: {fact['text']}"


def run_tool(conn, name: str, arguments: dict, seen: dict) -> str:
    """Run one tool call and return its result as text. Every fact returned is added to `seen`."""
    try:
        start = parse_time(arguments.get("start"))
        end = parse_time(arguments.get("end"))
    except ValueError:
        return f"error: start and end must be {ISO_TIME}"

    note = ""

    if name == "search":
        facts = search(conn, arguments["query"], SEARCH_RESULTS, start, end)
    else:
        everything = db.timeline(conn, start, end)
        facts = thin(everything)

        if len(facts) < len(everything):
            note = f"Showing {len(facts)} of {len(everything)}. Ask for a shorter stretch to see every moment.\n"

    seen.update({fact["id"]: fact for fact in facts})

    if not facts:
        return "Nothing was recorded that matches."

    return note + "\n".join(fact_line(fact) for fact in facts)


def check_sources(cited: list[int], seen: dict) -> list[dict]:
    """Return the cited facts, in order and once each, leaving out any number the model was never shown."""
    return [seen[fact_id] for fact_id in dict.fromkeys(cited) if fact_id in seen]


def ask_agent(conn, question: str, model: str = AGENT_MODEL) -> dict:
    """Answer a question by letting the model call the tools."""
    started = time.monotonic()
    prompt = instructions(conn, AGENT_NOTES)

    seen: dict = {}
    calls = []
    usd = 0.0

    send = [{"role": "user", "content": question}]
    previous_id = None

    for round_number in range(MAX_ROUNDS + 1):
        # Each request carries only what is new; previous_response_id links it to the conversation so far.
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
    started = time.monotonic()

    facts = search(conn, question, PLAIN_RESULTS)
    seen = {fact["id"]: fact for fact in facts}
    listing = "\n".join(fact_line(fact) for fact in facts) or "Nothing was recorded that matches."

    response = llm.client().responses.parse(
        model=model,
        instructions=instructions(conn, PLAIN_NOTES),
        input=f"Question: {question}\n\nFacts:\n{listing}",
        text_format=Answer,
        reasoning={"effort": "low"},
    )
    usd = llm.log_cost(conn, "rag", model, response.usage)

    return finish(response.output_parsed, seen, [], usd, 1, started)


def finish(answer: Answer, seen: dict, calls: list, usd: float, model_calls: int, started: float) -> dict:
    """Bundle an answer with its checked sources and what it took to get. usd leaves out the question's
    embedding, which costs well under a thousandth of a cent."""
    return {
        "answer": answer.answer,
        "found": answer.found,
        "sources": check_sources(answer.sources, seen),
        "calls": calls,
        "model_calls": model_calls,
        "usd": usd,
        "seconds": time.monotonic() - started,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Answer a question from what the glasses recorded.")
    parser.add_argument("question")
    parser.add_argument("--plain", action="store_true", help="one search and one model call, with no tools")
    parser.add_argument("--model", default=AGENT_MODEL)
    args = parser.parse_args()

    with db.connect() as conn:
        result = (ask_plain if args.plain else ask_agent)(conn, args.question, args.model)

    print(result["answer"] if result["found"] else f"(not found) {result['answer']}")

    if result["sources"]:
        print("\nSources:")

    for fact in result["sources"]:
        print(f"  [{fact['id']}] {fact['at'].astimezone():%a %d %b %H:%M:%S}, {clock(fact['offset_seconds'])} into"
              f" {fact['session_id']}\n      {fact['text']}")

        if fact["image_path"]:
            print(f"      {fact['image_path']}")

    if result["calls"]:
        print("\nTool calls:")

    for name, arguments in result["calls"]:
        given = ", ".join(f"{key}={value!r}" for key, value in arguments.items() if value is not None)
        print(f"  {name}({given})")

    print(f"\n{result['model_calls']} model calls, {result['seconds']:.1f} s, ${result['usd']:.5f}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
