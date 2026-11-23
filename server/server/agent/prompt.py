"""What the model is told: who it answers for, what the facts are, how to answer, and which recordings exist."""

from datetime import datetime

from server import db
from server.agent.reading import recording_line
from server.shared.gps import place_for

RECORDINGS_CHARACTERS = 4_000

INSTRUCTIONS = """\
You answer questions for someone who wears camera glasses. The glasses record what the wearer sees and hears. "I"
and "me" in a question mean the wearer, who appears in facts as Me.

The record is kept as facts. Each starts with its number in [brackets], the time it was recorded or the times it
covers, and its kind:
- frame: what was happening in one photo
- object, person, text: a thing, someone in view, or readable text, once per stretch of a scene, with where it was
- speech: words spoken; speakers are letters, not names
- said, conversation: what a passage or a whole conversation was about
- summary, activity, day: what happened in a scene, in a run of scenes, or in a whole day
- recording: a whole recording's title, overview, start, end and length
Facts name the recording they come from and the room when it is known; facts from phone recordings also say
where, in metres. Things worn or held by someone recognised say whose.

Answer only from the facts. Do not guess or add outside knowledge. A search always returns its closest matches,
even when nothing relevant was recorded, so check that a fact really answers the question. If the facts do not
answer it, set found to false, say the record does not show it, and leave sources empty. Do not pad the answer.

Keep the answer short and direct. If something changed over time, say what the latest fact shows and when. After
each claim put the number of the fact that backs it in brackets, like [412], and list those numbers in sources in
the order first used.

The app shows the answer with its sources. In highlight give the number of the one fact whose photo or words best
show the answer, and why in a few words in highlight_reason; leave both null if none stands out. Pick a layout:
photo when one picture shows it, quote when it is what someone said, people when it is about who was there, place
when it is about where, timeline when it is about a stretch of time or a sequence, and text otherwise. Write two or
three short follow-up questions in your own words. When found is false, give two or three next_steps, each a short
label for another way to look with the full question to ask; otherwise leave next_steps empty.

It is now {now}. Times without a UTC offset are read as this local time.

Recordings, newest first:
{recordings}"""

TOOL_NOTES = """

The question comes with the facts a search for it found. Answer from them if they are enough. Otherwise call the
tools until you can answer; if a search finds nothing relevant, try other words before giving up. Earlier messages
can tell you what a follow-up question refers to, but answer only from the facts.

Tools:
{tools}"""

PLAIN_NOTES = """

You are given the facts that one search found for the question. You cannot search again."""


def recordings_text(conn, moments: list[str] | None) -> str:
    """List the recordings newest first while they fit, then say how many earlier ones there are and how far back."""
    places = db.gps_places(conn)
    recordings = db.recording_list(conn, moments)
    lines, used = [], 0

    for shown, recording in enumerate(recordings):
        line = recording_line(recording, place_for(recording["lat"], recording["lon"], places))
        used += len(line) + 1

        if used > RECORDINGS_CHARACTERS:
            rest = recordings[shown:]
            lines.append(f"- and {len(rest)} earlier, back to {rest[-1]['start'].astimezone():%a %d %b %Y}")
            break

        lines.append(line)

    return "\n".join(lines) or "- none yet"


def instructions(conn, notes: str, moments: list[str] | None = None) -> str:
    """Return the instructions with the time, the recordings and the notes for this way of answering."""
    now = datetime.now().astimezone()

    return INSTRUCTIONS.format(now=f"{now:%A %d %B %Y %H:%M} (UTC{now:%z})", recordings=recordings_text(conn, moments)) + notes


def tool_notes(tools: list) -> str:
    """Return the notes for answering with tools, naming each tool and when to use it."""
    return TOOL_NOTES.format(tools="\n".join(f"- {tool.name}: {tool.guide}" for tool in tools))
