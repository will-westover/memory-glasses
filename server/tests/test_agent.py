from memory.agent import check_sources, thin


def fact(fact_id: int, kind: str = "moment") -> dict:
    return {"id": fact_id, "kind": kind}


def test_sources_the_model_was_never_shown_are_dropped():
    seen = {1: fact(1), 2: fact(2)}

    assert check_sources([2, 99, 1], seen) == [fact(2), fact(1)]


def test_a_source_cited_twice_is_listed_once():
    seen = {1: fact(1)}

    assert check_sources([1, 1], seen) == [fact(1)]


def test_short_timeline_is_left_alone():
    facts = [fact(1), fact(2, "speech"), fact(3)]

    assert thin(facts, limit=3) == facts


def test_long_timeline_keeps_all_speech_and_evenly_spaced_moments():
    facts = [fact(number, "speech" if number % 5 == 0 else "moment") for number in range(1, 21)]

    kept = thin(facts, limit=10)

    assert [item["id"] for item in kept if item["kind"] == "speech"] == [5, 10, 15, 20]
    assert [item["id"] for item in kept if item["kind"] == "moment"] == [1, 4, 8, 12, 16, 19]
    assert len(kept) <= 10
