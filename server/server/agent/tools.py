"""The tools the model can call: each one's description, what its step says in the app, and what it does."""

import json
from collections.abc import Callable
from dataclasses import dataclass

from server import db
from server.shared.config import NEIGHBOUR_METRES
from server.agent.reading import NOTHING_FOUND, coverage_text, fact_lines, parse_time, spoken_time
from server.agent.search import search_within

SEARCH_RESULTS = 12

TIMELINE_CHARACTERS = 12_000

OVERVIEW_KINDS = ("day", "recording", "activity", "summary")

ISO_TIME = "ISO 8601, e.g. 2026-11-11T19:54:00"

SUMMARISED = "This stretch is long, so it is summarised. Ask for a shorter stretch to see more."

BAD_TIMES = f"error: start and end must both be given, {ISO_TIME}"


@dataclass
class Asking:
    """What every tool call in one answer shares."""

    conn: object
    moments: list[str] | None = None
    window: tuple = (None, None)

    def only_asked(self, facts: list[dict]) -> list[dict]:
        """Keep the facts from the recordings this answer is limited to, if it is limited."""
        return facts if self.moments is None else [fact for fact in facts if fact["moment_id"] in self.moments]


@dataclass
class Tool:
    name: str
    guide: str
    parameters: dict
    step: Callable[[dict], str]
    run: Callable[[Asking, dict], tuple[str, list[dict]]]

    def schema(self) -> dict:
        """Return the tool as the model is given it."""
        required = list(self.parameters)
        properties = {name: {"type": kind, "description": text} for name, (kind, text) in self.parameters.items()}
        parameters = {"type": "object", "properties": properties, "required": required, "additionalProperties": False}

        return {"type": "function", "name": self.name, "description": self.guide, "strict": True, "parameters": parameters}


def between(arguments: dict) -> str:
    return f"from {spoken_time(arguments.get('start'))} to {spoken_time(arguments.get('end'))}"


def times(arguments: dict) -> tuple:
    return parse_time(arguments.get("start")), parse_time(arguments.get("end"))


def fits(facts: list[dict]) -> bool:
    return len(fact_lines(facts)) <= TIMELINE_CHARACTERS


def fit_timeline(facts: list[dict], fits: Callable[[list[dict]], bool] = fits) -> list[dict]:
    """Return every fact but scene summaries if they fit; otherwise only the broadest overviews that fit: days,
    recordings, activities and scene summaries, then fewer of those kinds, then as many days as fit."""
    detail = [fact for fact in facts if fact["kind"] != "summary"]

    if fits(detail):
        return detail

    for kinds in (OVERVIEW_KINDS, OVERVIEW_KINDS[:3], OVERVIEW_KINDS[:2]):
        broad = [fact for fact in facts if fact["kind"] in kinds]

        if fits(broad):
            return broad

    days = [fact for fact in facts if fact["kind"] == "day"]

    while days and not fits(days):
        days.pop()

    return days


def search(asking: Asking, arguments: dict) -> tuple[str, list[dict]]:
    start, end = times(arguments)
    window = (start, end) if start and end else asking.window
    facts = search_within(asking.conn, arguments["query"], SEARCH_RESULTS, window, asking.moments)

    return fact_lines(facts), facts


def timeline(asking: Asking, arguments: dict) -> tuple[str, list[dict]]:
    start, end = times(arguments)

    if not start or not end:
        return BAD_TIMES, []

    every = db.timeline(asking.conn, start, end, asking.moments)
    facts = fit_timeline(every)
    thinned = len(facts) < len([fact for fact in every if fact["kind"] != "summary"])

    return (f"{SUMMARISED}\n" if thinned else "") + fact_lines(facts), facts


def now_line(seen: dict | None) -> str:
    """Say where a thing or person was last seen, as the first line of the entity tool's answer."""
    if seen is None:
        return ""

    room = seen["room"] if seen["room"] and seen["room"].lower() not in ("room", (seen["place"] or "").lower()) else None
    where = ", ".join(part for part in (seen["place"], room and f"room: {room}", seen["where"]) if part)
    details = f" ({seen['details']})" if seen["details"] else ""
    near = f"; near {', '.join(seen['near'])}" if seen.get("near") else ""

    return f"Last seen {seen['at'].astimezone():%a %d %b %Y %H:%M}: {where or 'place unknown'}{near}{details}.\n"


def entity(asking: Asking, arguments: dict) -> tuple[str, list[dict]]:
    facts = asking.only_asked(db.entity_facts(asking.conn, arguments["fact_id"]))

    seen = db.last_seen(asking.conn, arguments["fact_id"], asking.moments)

    if seen:
        close = db.close_things(asking.conn, seen["entity_id"], NEIGHBOUR_METRES, False)
        seen["near"] = [f"{other['name']} ({other['metres']:.1f} m)" for other in close if other["metres"] > 0][:3]

    return now_line(seen) + fact_lines(facts), facts


def nearby(asking: Asking, arguments: dict) -> tuple[str, list[dict]]:
    metres = arguments.get("metres") or NEIGHBOUR_METRES
    thing = db.fact_entity(asking.conn, arguments["fact_id"])
    close = [other["id"] for other in db.close_things(asking.conn, thing, metres, False)] if thing else []
    by_spot = db.thing_facts(asking.conn, close) if close else []
    by_camera = db.nearby_facts(asking.conn, arguments["fact_id"], metres)
    seen = {fact["id"] for fact in by_spot}
    facts = asking.only_asked(by_spot + [fact for fact in by_camera if fact["id"] not in seen])

    return fact_lines(facts), facts


def coverage(asking: Asking, arguments: dict) -> tuple[str, list[dict]]:
    start, end = times(arguments)

    if not start or not end:
        return BAD_TIMES, []

    return coverage_text(db.coverage(asking.conn, start, end, asking.moments), start, end), []


def search_step(arguments: dict) -> str:
    where = f" {between(arguments)}" if arguments.get("start") else ""

    return f'Searching for "{arguments.get("query", "")}"{where}'


START = ("string", f"Start of the stretch. {ISO_TIME}")
END = ("string", f"End of the stretch. {ISO_TIME}")
FACT = ("integer", "The number of a fact")

TOOLS = [
    Tool(
        "search",
        "Find facts by what they are about, best matches first. With no times it looks in the time the question "
        "names, if any, and then everywhere.",
        {
            "query": ("string", "What to look for, in a few plain words"),
            "start": (["string", "null"], f"Only facts at or after this time. {ISO_TIME}"),
            "end": (["string", "null"], f"Only facts at or before this time. {ISO_TIME}"),
        },
        search_step,
        search,
    ),
    Tool(
        "timeline",
        "List what happened between two times, in order. Use it for a stretch of time, or for what came before or "
        "after something. A long stretch comes back as summaries.",
        {"start": START, "end": END},
        lambda arguments: f"Looking at what happened {between(arguments)}",
        timeline,
    ),
    Tool(
        "entity",
        "Say where the thing or person an object or person fact is about was last seen, then list every time it was "
        "seen and what changed about it, oldest first, across all recordings. Use it for where something is now, when "
        "it was last seen, whether it was there another time, or what changed.",
        {"fact_id": ("integer", "The number of an object or person fact")},
        lambda arguments: "Looking at every time one thing was seen",
        entity,
    ),
    Tool(
        "coverage",
        "List the recordings and scenes between two times with exact lengths, and how much was not recorded. Use it "
        "for how long, how often, what a day held, and before saying something was not recorded.",
        {"start": START, "end": END},
        lambda arguments: f"Adding up what was recorded {between(arguments)}",
        coverage,
    ),
    Tool(
        "nearby",
        "List what was seen close to a thing (by where things stand in the room, when that is known) or close to where "
        "a frame fact's photo was taken, in any recording at the same mapped place.",
        {
            "fact_id": ("integer", "The number of a frame or object fact"),
            "metres": (["number", "null"], f"How far around to look; {NEIGHBOUR_METRES} if null"),
        },
        lambda arguments: "Looking at what was nearby",
        nearby,
    ),
]

BY_NAME = {tool.name: tool for tool in TOOLS}


def read_arguments(text: str) -> dict | None:
    """Read a tool call's arguments, or None if they are not a JSON object."""
    try:
        arguments = json.loads(text)
    except json.JSONDecodeError:
        return None

    return arguments if isinstance(arguments, dict) else None


def run_tool(asking: Asking, name: str, arguments: dict | None) -> tuple[str, list[dict]]:
    """Run one tool call and return the text the model reads and the facts it found."""
    tool = BY_NAME.get(name)

    if tool is None:
        return f"error: there is no tool named {name}", []

    if arguments is None:
        return "error: the arguments were not a JSON object", []

    try:
        output, facts = tool.run(asking, arguments)
    except (KeyError, ValueError) as error:
        return f"error: {error}", []

    return output or NOTHING_FOUND, facts


def step_text(name: str, arguments: dict | None) -> str:
    """Describe one tool call in plain words."""
    tool = BY_NAME.get(name)

    return tool.step(arguments or {}) if tool else f"Using {name}"
