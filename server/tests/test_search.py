from memory.search import fuse, spread


def test_item_in_both_lists_beats_item_at_the_top_of_one():
    by_meaning = ["a", "b", "c"]
    by_keyword = ["d", "b"]

    ranked = [item for item, _ in fuse([by_meaning, by_keyword])]

    assert ranked[0] == "b"


def test_earlier_position_scores_higher_within_one_list():
    ranked = fuse([["first", "second", "third"]])

    assert [item for item, _ in ranked] == ["first", "second", "third"]
    assert ranked[0][1] > ranked[1][1] > ranked[2][1]


def test_empty_lists_give_no_results():
    assert fuse([[], []]) == []


def test_one_stretch_gives_at_most_two_facts_of_each_kind():
    def fact(fact_id, kind, offset, moment="m"):
        return {"id": fact_id, "kind": kind, "offset_seconds": offset, "moment_id": moment}

    facts = [
        fact(1, "object", 1),
        fact(2, "object", 2),
        fact(3, "object", 3),
        fact(4, "person", 4),
        fact(5, "object", 70),
    ]

    kept = spread(facts, {"m": [0.0, 60.0]}, limit=10)

    assert [item["id"] for item in kept] == [1, 2, 4, 5]


def test_a_window_time_without_an_offset_is_local_and_bad_text_is_none():
    from memory.when import local_time

    assert local_time("2026-11-20T17:00:00").utcoffset() is not None
    assert local_time("Friday evening") is None
    assert local_time(None) is None
