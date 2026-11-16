from memory.agent import check_sources, fit


def fact(fact_id: int, kind: str = "moment") -> dict:
    return {"id": fact_id, "kind": kind}


def test_sources_the_model_was_never_shown_are_dropped():
    seen = {1: fact(1), 2: fact(2)}

    assert check_sources([2, 99, 1], seen) == [fact(2), fact(1)]


def test_a_source_cited_twice_is_listed_once():
    seen = {1: fact(1)}

    assert check_sources([1, 1], seen) == [fact(1)]


def test_short_timeline_shows_detail_without_summaries():
    facts = [fact(1, "summary"), fact(2), fact(3, "speech")]

    assert fit(facts, limit=5) == [fact(2), fact(3, "speech")]


def test_long_timeline_shows_summaries_instead():
    facts = [fact(1, "summary"), fact(2), fact(3), fact(4, "summary"), fact(5), fact(6)]

    assert fit(facts, limit=3) == [fact(1, "summary"), fact(4, "summary")]
