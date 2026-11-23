from server.agent.search import fuse, spread


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


def test_one_scene_gives_one_fact_per_thing_and_two_of_each_other_kind():
    def fact(fact_id, kind, scene, entity=None):
        return {"id": fact_id, "kind": kind, "moment_id": "m", "episode_id": scene, "entity_id": entity}

    facts = [
        fact(1, "object", 10, entity=7),
        fact(2, "object", 10, entity=7),
        fact(3, "object", 11, entity=7),
        fact(4, "frame", 10),
        fact(5, "frame", 10),
        fact(6, "frame", 10),
    ]

    assert [item["id"] for item in spread(facts, limit=10)] == [1, 3, 4, 5]


def test_a_window_time_without_an_offset_is_local_and_bad_text_is_none():
    from server.agent.when import local_time

    assert local_time("2026-11-20T17:00:00").utcoffset() is not None
    assert local_time("Friday evening") is None
    assert local_time(None) is None
