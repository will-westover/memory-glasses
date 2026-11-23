"""How facts, recordings and times read to the model and in the app's steps."""

from datetime import datetime, timedelta

from server.config import spoken_length

NOTHING_FOUND = "Nothing was recorded that matches."


def parse_time(text: str | None) -> datetime | None:
    """Read an ISO 8601 time. One without a UTC offset is taken as local time."""
    if not text:
        return None

    at = datetime.fromisoformat(text)

    return at if at.tzinfo else at.astimezone()


def spoken_time(at: datetime | str | None) -> str:
    """Return a time the way a person would say it, like Tue 7:15 pm."""
    at = parse_time(at) if isinstance(at, str) or at is None else at

    return f"{at:%a} {at:%-I:%M} {at:%p}".replace("AM", "am").replace("PM", "pm") if at else "?"


def recording_note(fact: dict) -> str:
    """Name the recording a fact comes from, with when it ran, so facts from different recordings stay apart."""
    if not fact.get("moment_start") or fact["kind"] == "recording":
        return ""

    start = fact["moment_start"].astimezone()
    end = start + timedelta(seconds=fact["moment_seconds"])

    return f'  [recording "{fact.get("moment_title") or "untitled"}", {start:%H:%M}-{end:%H:%M}]'


def fact_line(fact: dict) -> str:
    """Return a fact as the one line the model reads."""
    where = ""

    if fact.get("position"):
        x, z = fact["position"]
        where = f"  ({fact.get('room') or 'room'}, at {x:.1f} m, {z:.1f} m)"

    start = fact["at"].astimezone()
    seconds = (fact.get("end_offset_seconds") or fact["offset_seconds"]) - fact["offset_seconds"]
    until = f" to {start + timedelta(seconds=seconds):%H:%M:%S}" if seconds >= 1 else ""

    return f"[{fact['id']}] {start:%a %d %b %Y %H:%M:%S}{until}  {fact['kind']}: {fact['text']}{where}{recording_note(fact)}"


def fact_lines(facts: list[dict]) -> str:
    """Return facts as the text the model reads."""
    return "\n".join(fact_line(fact) for fact in facts) if facts else NOTHING_FOUND


def fact_record(fact: dict) -> dict:
    """Return a fact as JSON a stored message can keep."""
    return {
        "id": fact["id"],
        "kind": fact["kind"],
        "at": fact["at"].isoformat(),
        "moment_id": fact["moment_id"],
        "offset_seconds": fact["offset_seconds"],
        "end_offset_seconds": fact.get("end_offset_seconds"),
        "text": fact["text"],
        "image_path": fact["image_path"],
        "frame_id": fact.get("frame_id"),
    }


def recording_line(recording: dict, place: str | None) -> str:
    """Return one recording as a line of the instructions."""
    start = recording["start"].astimezone()
    end = start + timedelta(seconds=recording["seconds"])
    named = f'"{recording["title"]}", ' if recording["title"] else ""
    at = f", at {place}" if place else ""

    return f"- {named}{start:%a %d %b %Y %H:%M:%S} to {end:%H:%M:%S} ({spoken_length(recording['seconds'])}){at}"


def coverage_text(recordings: list[dict], start: datetime, end: datetime) -> str:
    """Describe the recordings in a range, with scene lengths and totals worked out, as the model reads it."""
    if not recordings:
        return f"Nothing was recorded between {start:%a %d %b %H:%M} and {end:%a %d %b %H:%M}."

    lines, recorded = [], 0.0

    for recording in recordings:
        began, ended = max(recording["start"], start), min(recording["end"], end)
        seconds = max((ended - began).total_seconds(), 0)
        recorded += seconds
        lines.append(
            f'Recording "{recording["title"] or "untitled"}", {recording["start"].astimezone():%a %d %b %H:%M:%S} to'
            f" {recording['end'].astimezone():%H:%M:%S} ({spoken_length(seconds)}): {recording['overview'] or ''}"
        )

        for scene in recording["scenes"]:
            scene_start, scene_end = datetime.fromisoformat(scene["start"]), datetime.fromisoformat(scene["end"])
            length = spoken_length((scene_end - scene_start).total_seconds())
            lines.append(
                f"  - {scene_start.astimezone():%H:%M:%S} to {scene_end.astimezone():%H:%M:%S} ({length}): {scene['title']}"
            )

    total = (end - start).total_seconds()
    unrecorded = spoken_length(max(total - recorded, 0))
    lines.append(
        f"Recorded {spoken_length(recorded)} of the {spoken_length(total)} asked about; not recorded {unrecorded}."
    )

    return "\n".join(lines)
