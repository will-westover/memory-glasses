from datetime import datetime, timedelta, timezone

import numpy as np
import pytest

from memory.ingest.facts import (
    Said,
    Stretch,
    Thing,
    frame_fact,
    group_frames,
    group_passages,
    said_facts,
    stretch_facts,
    stretches,
)

ZONE = timezone(timedelta(hours=-7))
EVENING = datetime(2026, 11, 10, 19, 54, tzinfo=ZONE)


def line(start, end, text, speaker="A"):
    return {"at": EVENING, "start": start, "end": end, "speaker": speaker, "text": text}


def frame(frame_id, offset, objects=(), texts=(), room=None, people=()):
    return {
        "id": frame_id,
        "at": EVENING,
        "offset_seconds": offset,
        "summary": "Making coffee. Steam rises.",
        "objects": [{"name": name, "location": location} for name, location in objects],
        "texts": list(texts),
        "room": room,
        "people": [{"name": name} for name in people],
    }


ALIKE = np.array([1.0, 0.0])
DIFFERENT = np.array([0.0, 1.0])


def test_short_lines_are_joined_into_one_passage():
    lines = [line(0, 2, "And um"), line(2, 5, "this is where I sleep"), line(5, 7, "it's comfortable")]

    assert group_passages(lines) == [lines]


def test_a_long_pause_starts_a_new_passage():
    lines = [line(0, 2, "first thought"), line(30, 32, "much later")]

    assert group_passages(lines) == [[lines[0]], [lines[1]]]


def test_a_box_is_kept_inside_the_picture():
    from memory.db.moments import clean_box

    assert clean_box([0.9, -0.1, 0.5, 0.3]) == [0.9, 0.0, pytest.approx(0.1), 0.3]
    assert clean_box([0.1, 0.2]) is None


def test_a_frame_fact_names_the_people_and_room():
    fact = frame_fact(frame(1, 0.0, room="Kitchen", people=["Friend 1"]), "Home")

    assert fact["text"] == "Making coffee. Steam rises. With Friend 1. Room: Kitchen. At Home."
    assert fact["frame_ids"] == [1]


def test_a_passage_stops_before_it_gets_too_long():
    forty_words = " ".join(["word"] * 40)
    lines = [line(0, 10, forty_words), line(10, 20, forty_words), line(20, 21, "short")]

    assert group_passages(lines) == [[lines[0]], [lines[1], lines[2]]]


def test_frames_that_look_alike_in_one_room_are_one_stretch():
    frames = [frame(1, 0.0, room="Kitchen"), frame(2, 6.0, room="Kitchen"), frame(3, 12.0, room="Kitchen")]

    assert group_frames(frames, [ALIKE] * 3) == [([0, 1, 2], "end")]


def test_a_stretch_ends_when_the_room_changes():
    frames = [frame(1, 0.0, room="Kitchen"), frame(2, 6.0, room="Hallway")]

    assert group_frames(frames, [ALIKE, ALIKE]) == [([0], "room changed"), ([1], "end")]


def test_a_stretch_ends_after_a_long_gap():
    frames = [frame(1, 0.0), frame(2, 100.0)]

    assert group_frames(frames, [ALIKE, ALIKE]) == [([0], "gap"), ([1], "end")]


def test_a_stretch_ends_when_it_gets_too_long():
    frames = [frame(index, index * 20.0) for index in range(8)]

    groups = group_frames(frames, [ALIKE] * 8)

    assert [members for members, _ in groups] == [[0, 1, 2, 3, 4, 5], [6, 7]]
    assert groups[0][1] == "length"


def test_a_stretch_ends_when_the_photo_looks_different():
    frames = [frame(1, 0.0), frame(2, 6.0), frame(3, 12.0)]

    assert group_frames(frames, [ALIKE, ALIKE, DIFFERENT]) == [([0, 1], "looks different"), ([2], "end")]


def test_speech_joins_the_stretch_it_starts_in():
    frames = [frame(1, 0.0, room="Kitchen"), frame(2, 40.0, room="Hallway")]
    early, late = [line(-3, 2, "before the first photo")], [line(45, 50, "in the hallway")]

    groups = stretches(frames, [ALIKE, ALIKE], [early, late])

    assert groups == [([frames[0]], [early]), ([frames[1]], [late])]


def test_speech_without_photos_is_split_by_length():
    passages = [[line(0, 5, "one")], [line(60, 65, "two")], [line(130, 135, "three")]]

    assert [len(heard) for _, heard in stretches([], [], passages)] == [2, 1]


def test_a_stretch_gives_each_thing_person_and_text_once():
    frames = [
        frame(1, 0.0, texts=[("EXIT", "sign")], people=["Friend 1"]),
        frame(2, 6.0, texts=[("EXIT", "sign")], people=["Friend 1", "Friend 2"]),
    ]
    stretch = Stretch(
        title="Coffee",
        happened="They make coffee. It is early.",
        things=[Thing(name="edamame", where="in a bowl", photos=[1, 2]), Thing(name="stray", where="x", photos=[9])],
        said=[],
    )

    facts = stretch_facts(frames, [], "Home", stretch)
    by_kind = {}
    for fact in facts:
        by_kind.setdefault(fact["kind"], []).append(fact)

    assert by_kind["summary"][0]["title"] == "Coffee"
    assert by_kind["summary"][0]["end_offset_seconds"] == 6.0
    assert [fact["text"] for fact in by_kind["object"]] == ["edamame: in a bowl. At Home. They make coffee."]
    assert by_kind["object"][0]["frame_ids"] == [1, 2]
    assert {fact["text"].split(".")[0]: fact["frame_ids"] for fact in by_kind["person"]} == {
        "Friend 1 was there": [1, 2],
        "Friend 2 was there": [2],
    }
    assert len(by_kind["text"]) == 1


def test_without_a_model_answer_objects_are_merged_by_name():
    frames = [frame(1, 0.0, objects=[("Mug", "on desk")]), frame(2, 6.0, objects=[("mug", "in hand")])]

    facts = stretch_facts(frames, [], None, None)

    assert [(fact["kind"], fact["frame_ids"]) for fact in facts] == [("object", [1, 2])]


def test_speech_summaries_go_to_the_passage_they_name():
    first, second, third = [line(0, 2, "one")], [line(10, 12, "two")], [line(20, 22, "three")]
    said = [Said(passage=3, text="About three."), Said(passage=1, text="About one."), Said(passage=9, text="Stray.")]

    facts = said_facts([first, second, third], said, None)

    assert [(fact["offset_seconds"], fact["text"]) for fact in facts] == [(0, "About one."), (20, "About three.")]
