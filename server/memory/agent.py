"""Answer a question from what the glasses recorded."""

import argparse
import json
import math
import sys
import time
from datetime import datetime, timedelta

from typing import Literal

from pydantic import BaseModel, Field

from memory import db, llm
from memory.citations import partial_string, renumber, shown_so_far
from memory.config import AGENT_MODEL, minutes_seconds
from memory.gps import place_for
from memory.search import search

MAX_TOOL_ROUNDS = 5

SEARCH_RESULTS = 8
PLAIN_RESULTS = 10

TIMELINE_MAX_FACTS = 60

MOMENTS_IN_PROMPT = 20

INSTRUCTIONS = """\
You answer questions for someone who wears camera glasses. The glasses record what the wearer sees and hears,
and that record is kept as facts: frames (what was happening in each photo), objects and where each one was, text that was
readable, speech, summaries of what happened and what was said, and which people the wearer has named
were in view. Each fact starts with its number in [brackets] and the time it was recorded. "I" and "me"
in a question mean the wearer. Speakers are labelled with letters, not names.

Answer only from the facts. Do not guess or add outside knowledge. A search always returns its closest matches,
even when nothing relevant was recorded, so check that a fact really answers the question before relying on it.
If the facts do not answer the question, set found to false, say the record does not show it, and leave sources
empty. Do not pad the answer with other things that were recorded.

Keep the answer short and direct. If facts differ because something changed over time, say what the latest one
shows and when. After each claim, put the number of the fact that backs it in brackets, like [412], and list those
numbers in sources in the order you first use them.

The app shows the answer with its sources. In highlight, give the number of the one fact whose photo or words best
show the answer, and say why in a few words in highlight_reason; leave both null if none stands out. Choose a layout
for how the app presents the answer: photo when one picture shows it, quote when it is mainly what someone said,
people when it is about who was there, place when it is about where, timeline when it is about a stretch of time or
a sequence of events, and text otherwise. Write two or three short follow-up questions the wearer might ask next, in
your own words. When found is false, give two or three next_steps: a short label for another way to look, such as a
wider stretch of time or other words, each with the full question to ask. Leave next_steps empty when found is true.

It is now {now}. Times without a UTC offset are read as this local time.
Recordings:
{recordings}"""

AGENT_NOTES = """

You have three tools. search finds facts by what they are about. timeline lists what happened between two times,
in order: use it for questions about a stretch of time, or about what came before or after something. nearby lists
what was seen close to where one fact's frame was taken, across every moment recorded in the same mapped place: use
it for questions about what was near something, or what was in the same spot another time. Facts say which room
they were in when it is known, and facts from phone recordings also say where, in metres. Call them
until you can answer. If a search finds nothing relevant, try other words before giving up. Earlier messages
in the conversation can tell you what a follow-up question refers to, but answer only from the facts."""

PLAIN_NOTES = """

You are given the facts that one search found for the question. You cannot search again."""

LONG_STRETCH = "This stretch is long, so it is summarised. Ask for a shorter stretch to see every frame."

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
        "description": "List what happened between two times, in order: each frame and what was said.",
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
    {
        "type": "function",
        "name": "nearby",
        "description": "List what was seen near where a fact's frame was taken, in any moment recorded in the same mapped place.",
        "strict": True,
        "parameters": {
            "type": "object",
            "properties": {
                "fact_id": {"type": "integer", "description": "The number of a fact from a frame"},
                "metres": {"type": ["number", "null"], "description": "How far around to look; 1.5 if null"},
            },
            "required": ["fact_id", "metres"],
            "additionalProperties": False,
        },
    },
]

NEARBY_DEFAULT_METRES = 1.5


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


def fill_instructions(conn, notes: str, moments: list[str] | None = None) -> str:
    """Return the instructions with the current time and the newest recordings filled in."""
    places = db.gps_places(conn)
    moment_rows = [row for row in db.list_moments(conn) if moments is None or row[0] in moments]
    lines = []

    for _id, _source, started_at, duration, _frames, _has_sound, lat, lon, _title in moment_rows[:MOMENTS_IN_PROMPT]:
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


def fit_timeline(facts: list[dict], limit: int = TIMELINE_MAX_FACTS) -> list[dict]:
    """Return the frames and speech if they fit in `limit`, otherwise the summaries of that stretch."""
    detail = [fact for fact in facts if fact["kind"] != "summary"]

    if len(detail) <= limit:
        return detail

    overview = [fact for fact in facts if fact["kind"] == "summary"] or detail

    return overview[:: math.ceil(len(overview) / limit)]


def fact_line(fact: dict) -> str:
    """Return a fact as the one line the model reads."""
    where = ""

    if fact.get("position"):
        x, z = fact["position"]
        where = f"  ({fact.get('room') or 'room'}, at {x:.1f} m, {z:.1f} m)"

    return f"[{fact['id']}] {fact['at'].astimezone():%a %d %b %Y %H:%M:%S}  {fact['kind']}: {fact['text']}{where}"


def fact_lines(facts: list[dict]) -> str:
    """Return facts as the text the model reads."""
    if not facts:
        return "Nothing was recorded that matches."

    return "\n".join(fact_line(fact) for fact in facts)


def find_facts(conn, name: str, arguments: dict, moments: list[str] | None = None) -> list[dict]:
    """Return the facts one tool call asks for, from only the given moments when some are named."""
    if name == "nearby":
        near = db.nearby_facts(conn, arguments["fact_id"], arguments.get("metres") or NEARBY_DEFAULT_METRES)

        return [fact for fact in near if moments is None or fact["moment_id"] in moments]

    start = parse_time(arguments.get("start"))
    end = parse_time(arguments.get("end"))

    if name == "search":
        return search(conn, arguments["query"], SEARCH_RESULTS, start, end, moments)

    if not start or not end:
        raise ValueError("timeline needs a start and an end")

    return fit_timeline(db.timeline(conn, start, end, moments))


def run_tool(conn, name: str, arguments: dict, seen: dict, moments: list[str] | None = None) -> tuple[str, list[dict]]:
    """Run one tool call, remember the facts it returned, and return them as text and as found."""
    try:
        facts = find_facts(conn, name, arguments, moments)
    except ValueError:
        return f"error: start and end must be {ISO_TIME}", []

    seen.update({fact["id"]: fact for fact in facts})

    if name == "timeline" and facts and facts[0]["kind"] == "summary":
        return f"{LONG_STRETCH}\n{fact_lines(facts)}", facts

    return fact_lines(facts), facts


def fact_record(fact: dict) -> dict:
    """Return a fact as JSON a stored message can keep."""
    return {
        "id": fact["id"],
        "kind": fact["kind"],
        "at": fact["at"].isoformat(),
        "moment_id": fact["moment_id"],
        "offset_seconds": fact["offset_seconds"],
        "text": fact["text"],
        "image_path": fact["image_path"],
        "frame_id": fact.get("frame_id"),
    }


def local_now() -> datetime:
    return datetime.now().astimezone()


def check_sources(cited: list[int], seen: dict) -> list[dict]:
    """Return the cited facts, once each, leaving out any the model was never shown."""
    return [seen[fact_id] for fact_id in dict.fromkeys(cited) if fact_id in seen]


def model_input(history: list[tuple[str, str]], question: str) -> list[dict]:
    """Return earlier questions and answers, then the new question, as model input."""
    messages = []

    for asked, answered in history:
        messages += [{"role": "user", "content": asked}, {"role": "assistant", "content": answered}]

    return messages + [{"role": "user", "content": question}]


def spoken_time(text: str | None) -> str:
    """Return a tool's time argument the way a person would say it."""
    at = parse_time(text)

    return f"{at:%a} {at:%-I:%M} {at:%p}".replace("AM", "am").replace("PM", "pm") if at else "?"


def step_text(name: str, arguments: dict) -> str:
    """Describe one tool call in plain words."""
    if name == "search":
        between = (
            f" from {spoken_time(arguments['start'])} to {spoken_time(arguments['end'])}"
            if arguments.get("start")
            else ""
        )

        return f'Searching for "{arguments["query"]}"{between}'

    if name == "timeline":
        return f"Looking at what happened from {spoken_time(arguments.get('start'))} to {spoken_time(arguments.get('end'))}"

    return "Looking at what was nearby"


def step_event(kind: str, text: str, facts: list[dict] = ()) -> tuple[str, dict]:
    """Return a step event with up to four of the photos its facts came from."""
    images = list(dict.fromkeys(fact["image_path"] for fact in facts if fact["image_path"]))[:4]

    return "step", {"kind": kind, "text": text, "image_paths": images}


def answer_events(
    conn,
    question: str,
    model: str = AGENT_MODEL,
    history: list[tuple[str, str]] = (),
    use_nearby: bool = True,
    frame: dict | None = None,
    moments: list[str] | None = None,
):
    """Answer a question by letting the model call the tools, yielding each step and piece of the answer as it comes."""
    llm.require_price(model)
    started = time.monotonic()
    prompt = fill_instructions(conn, AGENT_NOTES, moments)

    seen: dict = {frame["id"]: frame} if frame else {}
    calls = []
    usd = 0.0

    asked = f"{question}\n\nThe question is about this frame:\n{fact_line(frame)}" if frame else question
    round_input = model_input(list(history), asked)
    previous_id = None
    tools = TOOLS if use_nearby else [tool for tool in TOOLS if tool["name"] != "nearby"]
    messages = []

    for round_number in range(MAX_TOOL_ROUNDS + 1):
        request = {
            "model": model,
            "instructions": prompt,
            "input": round_input,
            "previous_response_id": previous_id,
            "tools": tools,
            "tool_choice": "auto" if round_number < MAX_TOOL_ROUNDS else "none",
        }

        streamed_json, sent_text = "", ""

        with llm.client().responses.stream(**request, text_format=Answer, reasoning={"effort": "low"}) as stream:
            for event in stream:
                if event.type != "response.output_text.delta":
                    continue

                if not streamed_json:
                    yield step_event("answer", "Writing the answer")

                streamed_json += event.delta
                text = shown_so_far(partial_string(streamed_json, "answer"), seen)

                if len(text) > len(sent_text) and text.startswith(sent_text):
                    yield "delta", {"text": text[len(sent_text) :]}
                    sent_text = text

            response = stream.get_final_response()

        received = response.model_dump(mode="json")

        with llm.collect_call_ids() as call_ids:
            usd += llm.log_cost(conn, "agent", model, response.usage, request=request, response=received)

        messages.append(
            {"role": "assistant", "at": local_now(), "content": {"output": received["output"]}, "calls": call_ids}
        )

        function_calls = [item for item in response.output if item.type == "function_call"]

        if not function_calls:
            break

        round_input = []
        previous_id = response.id

        for call in function_calls:
            arguments = json.loads(call.arguments)
            calls.append((call.name, arguments))
            yield step_event(call.name, step_text(call.name, arguments))

            with llm.collect_call_ids() as call_ids:
                output, facts = run_tool(conn, call.name, arguments, seen, moments)

            yield step_event(call.name, step_text(call.name, arguments), facts)

            round_input.append({"type": "function_call_output", "call_id": call.call_id, "output": output})
            messages.append(
                {
                    "role": "tool",
                    "at": local_now(),
                    "calls": call_ids,
                    "content": {
                        "call_id": call.call_id,
                        "name": call.name,
                        "arguments": arguments,
                        "output": output,
                        "facts": [fact_record(fact) for fact in facts],
                    },
                }
            )

    parsed = response.output_parsed or NO_ANSWER
    messages[-1]["content"]["answer"] = parsed.model_dump()

    yield "result", {**answer_result(parsed, seen, calls, usd, round_number + 1, started), "messages": messages}


def ask_agent(
    conn,
    question: str,
    model: str = AGENT_MODEL,
    history: list[tuple[str, str]] = (),
    use_nearby: bool = True,
    frame: dict | None = None,
    moments: list[str] | None = None,
) -> dict:
    """Answer a question by letting the model call the tools."""
    for kind, payload in answer_events(conn, question, model, history, use_nearby, frame, moments):
        if kind == "result":
            return payload


def ask_plain(conn, question: str, model: str = AGENT_MODEL, moments: list[str] | None = None) -> dict:
    """Answer a question from one search and one model call."""
    llm.require_price(model)
    started = time.monotonic()

    facts = search(conn, question, PLAIN_RESULTS, moments=moments)
    seen = {fact["id"]: fact for fact in facts}

    request = {
        "model": model,
        "instructions": fill_instructions(conn, PLAIN_NOTES, moments),
        "input": f"Question: {question}\n\nFacts:\n{fact_lines(facts)}",
    }
    response = llm.client().responses.parse(**request, text_format=Answer, reasoning={"effort": "low"})
    usd = llm.log_cost(conn, "rag", model, response.usage, request=request, response=response.model_dump(mode="json"))

    return answer_result(response.output_parsed, seen, [], usd, 1, started)


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


def print_sources(sources: list[dict]) -> None:
    if sources:
        print("\nSources:")

    for fact in sources:
        print(
            f"  [{fact['id']}] {fact['at'].astimezone():%a %d %b %H:%M:%S}, {minutes_seconds(fact['offset_seconds'])} into"
            f" {fact['moment_id']}\n      {fact['text']}"
        )

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
