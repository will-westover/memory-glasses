from memory.search import fuse


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
