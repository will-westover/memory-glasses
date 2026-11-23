"""Group a recording's scenes into activities and its speech into conversations, and sum up each day."""

from datetime import datetime, timedelta

from pydantic import BaseModel, Field

from server import db, llm
from server.config import SUMMARY_MODEL

ACTIVITY_INSTRUCTIONS = """\
You are given one recording from someone's camera glasses: its scenes in order, each with its times, a title, what
happened and who was there, and what was said, as numbered passages with times. "Me" is the wearer.

Group the scenes into activities: what the person was doing, the way they would describe their own day ("dinner
with Friend 1 and Friend 2", "working on the camera glasses"). An activity can be one scene or many; a new one starts
where what the person was doing changes, not where the view changes. Every scene belongs to exactly one activity.

Also group the passages into conversations: the passages that belong to one exchange, what it was about, and who
spoke. Use only what is given."""

DAY_INSTRUCTIONS = """\
You are given everything someone's camera glasses recorded on one day, as activities in order with their times.
Write a title of a few words for the day and two to four sentences on what the day consisted of, in order, saying
when each part happened. Mention only what is given; parts of the day that were not recorded are simply absent."""


class Activity(BaseModel):
    title: str = Field(description="A few words on what the person was doing")
    summary: str = Field(description="One or two sentences on what the activity involved and who was there")
    scenes: list[int] = Field(description="The numbers of the scenes that make up this activity")


class Conversation(BaseModel):
    passages: list[int] = Field(description="The numbers of the passages that make up this conversation")
    summary: str = Field(description="One or two sentences on what was talked about and who spoke")


class Grouping(BaseModel):
    activities: list[Activity]
    conversations: list[Conversation] = Field(description="Empty if nothing was said")


class Day(BaseModel):
    title: str = Field(description="A few words naming the day")
    summary: str = Field(description="Two to four sentences on what the day consisted of, in order")


def scene_lines(scenes: list[dict]) -> str:
    return "\n".join(
        f"{number}. {scene['start'].astimezone():%H:%M:%S} to {scene['end'].astimezone():%H:%M:%S}: {scene['title']}."
        f" {scene['summary']}" + (f" People: {', '.join(scene['people'])}." if scene["people"] else "")
        for number, scene in enumerate(scenes, start=1)
    )


def passage_lines(passages: list[list[dict]]) -> str:
    return "\n".join(
        f"{number}. {passage[0]['at'].astimezone():%H:%M:%S}: "
        + " ".join(f"{line['speaker'] or 'Someone'}: {line['text']}" for line in passage)
        for number, passage in enumerate(passages, start=1)
    )


def ask(instructions: str, text: str, answer_type, step: str, conn):
    """Make one text-only structured call and log its cost."""
    llm.require_price(SUMMARY_MODEL)
    response = llm.client().responses.parse(
        model=SUMMARY_MODEL, instructions=instructions, input=text, text_format=answer_type, reasoning={"effort": "low"}
    )
    llm.log_cost(conn, step, SUMMARY_MODEL, response.usage)

    return response.output_parsed


def activity_facts(moment_start, scenes: list[dict], grouping: Grouping, conn, moment_id: str) -> list[dict]:
    """Store each activity and return a fact for it, covering its scenes' frames."""
    facts = []
    db.forget_activities(conn, moment_id)

    for activity in grouping.activities:
        members = [scenes[number - 1] for number in sorted(set(activity.scenes)) if 0 < number <= len(scenes)]

        if not members:
            continue

        start, end = members[0]["start"], max(scene["end"] for scene in members)
        episode_id = db.save_activity(
            conn, moment_id, start, end, activity.title, activity.summary, [m["id"] for m in members]
        )
        facts.append(
            {
                "at": start,
                "offset_seconds": (start - moment_start).total_seconds(),
                "end_offset_seconds": (end - moment_start).total_seconds(),
                "text": f"{activity.title}: {activity.summary}",
                "title": activity.title,
                "episode_id": episode_id,
                "frame_ids": sorted({frame for scene in members for frame in scene["frames"]}),
            }
        )

    return facts


def conversation_facts(passages: list[list[dict]], grouping: Grouping) -> list[dict]:
    """Return a fact for each conversation, covering all of its passages."""
    facts = []

    for conversation in grouping.conversations:
        members = [passages[number - 1] for number in sorted(set(conversation.passages)) if 0 < number <= len(passages)]

        if members:
            facts.append(
                {
                    "at": members[0][0]["at"],
                    "offset_seconds": members[0][0]["start"],
                    "end_offset_seconds": members[-1][-1]["end"],
                    "text": f"Conversation: {conversation.summary}",
                }
            )

    return facts


def write_activities(conn, moment_id: str, passages: list[list[dict]]) -> dict[str, int]:
    """Group a recording's scenes into activities and its speech into conversations, store them, and update its day."""
    scenes = db.moment_scenes(conn, moment_id)

    if not scenes:
        return {}

    moment_start = db.moment_start_and_location(conn, moment_id)[0]
    text = f"Scenes:\n{scene_lines(scenes)}\n\nSaid:\n{passage_lines(passages) or 'Nothing.'}"
    grouping = ask(ACTIVITY_INSTRUCTIONS, text, Grouping, "activities", conn)

    if grouping is None:
        return {}

    activities = activity_facts(moment_start, scenes, grouping, conn, moment_id)
    conversations = conversation_facts(passages, grouping)
    texts = [fact["text"] for fact in activities + conversations]
    vectors = llm.embed(conn, texts, "embed") if texts else []

    db.add_facts(conn, moment_id, "activity", activities, vectors[: len(activities)])
    db.add_facts(conn, moment_id, "conversation", conversations, vectors[len(activities) :])
    conn.commit()

    write_day(conn, moment_start.astimezone().date())

    return {"activity": len(activities), "conversation": len(conversations)}


def write_day(conn, day) -> None:
    """Rewrite the summary of one local day from all of its activities, and store it as a fact."""
    start = datetime.combine(day, datetime.min.time()).astimezone()
    activities = db.day_activities(conn, start, start + timedelta(days=1))

    if not activities:
        return

    lines = "\n".join(
        f"- {item['start'].astimezone():%H:%M} to {item['end'].astimezone():%H:%M}: {item['title']}. {item['summary']}"
        for item in activities
    )
    written = ask(DAY_INSTRUCTIONS, f"{day:%A %d %B %Y}\n{lines}", Day, "day", conn)

    if written is None:
        return

    first, last = activities[0], activities[-1]
    episode_id = db.save_day(conn, day, first["start"], last["end"], written.title, written.summary)
    text = f"{day:%A %d %B}: {written.title}. {written.summary}"
    fact = {
        "kind": "day",
        "at": first["start"],
        "offset_seconds": 0,
        "end_offset_seconds": (last["end"] - first["start"]).total_seconds(),
        "text": text,
        "title": written.title,
        "episode_id": episode_id,
    }

    db.forget_day_facts(conn, list({item["moment_id"] for item in activities}))
    db.add_facts(conn, last["moment_id"], "day", [fact], llm.embed(conn, [text], "embed"))
    conn.commit()
