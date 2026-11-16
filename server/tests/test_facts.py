from datetime import datetime, timedelta, timezone

from memory.ingest.facts import Said, frame_facts, passages, said_facts, stretches

ZONE = timezone(timedelta(hours=-7))
EVENING = datetime(2026, 11, 10, 19, 54, tzinfo=ZONE)


def line(start, end, text, speaker="A"):
    return {"at": EVENING, "start": start, "end": end, "speaker": speaker, "text": text}


def frame(frame_id, offset, objects=(), texts=()):
    return {"id": frame_id, "at": EVENING, "offset_seconds": offset, "summary": "Making coffee. Steam rises.",
            "objects": list(objects), "texts": list(texts)}


def test_short_lines_are_joined_into_one_passage():
    lines = [line(0, 2, "And um"), line(2, 5, "this is where I sleep"), line(5, 7, "it's comfortable")]

    assert passages(lines) == [lines]


def test_a_long_pause_starts_a_new_passage():
    lines = [line(0, 2, "first thought"), line(30, 32, "much later")]

    assert passages(lines) == [[lines[0]], [lines[1]]]


def test_each_object_becomes_its_own_fact_with_context():
    facts = frame_facts([frame(1, 0.0, objects=[("bottle", "on counter"), ("mug", "next to sink")])], "Home")

    object_facts = [fact["text"] for fact in facts if fact["kind"] == "object"]

    assert len(object_facts) == 2
    assert object_facts[0] == "bottle: on counter. At Home. Making coffee."


def test_an_object_that_stays_put_is_not_repeated():
    bottle = ("bottle", "on counter")
    frames = [frame(1, 0.0, objects=[bottle]), frame(2, 2.0, objects=[bottle]), frame(3, 4.0, objects=[("bottle", "in bag")])]

    object_facts = [fact["text"].split(".")[0] for fact in frame_facts(frames, None) if fact["kind"] == "object"]

    assert object_facts == ["bottle: on counter", "bottle: in bag"]


def test_a_passage_stops_before_it_gets_too_long():
    forty_words = " ".join(["word"] * 40)
    lines = [line(0, 10, forty_words), line(10, 20, forty_words), line(20, 21, "short")]

    assert passages(lines) == [[lines[0]], [lines[1], lines[2]]]


def test_frames_and_speech_are_split_into_minutes():
    early, late = frame(1, 5.0), frame(2, 70.0)
    passage = [line(65, 68, "second minute")]

    assert stretches([early, late], [passage]) == [([early], []), ([late], [passage])]


def test_speech_summaries_go_to_the_passage_they_name():
    first, second, third = [line(0, 2, "one")], [line(10, 12, "two")], [line(20, 22, "three")]
    said = [Said(passage=3, text="About three."), Said(passage=1, text="About one."), Said(passage=9, text="Stray.")]

    facts = said_facts([first, second, third], said, None)

    assert [(fact["offset_seconds"], fact["text"]) for fact in facts] == [(0, "About one."), (20, "About three.")]
