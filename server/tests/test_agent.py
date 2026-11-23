from server.agent.tools import Asking, fit_timeline, read_arguments, run_tool


def fact(fact_id: int, kind: str = "frame") -> dict:
    return {"id": fact_id, "kind": kind}


def at_most(count: int):
    return lambda facts: len(facts) <= count


def test_short_timeline_shows_detail_without_summaries():
    facts = [fact(1, "summary"), fact(2), fact(3, "speech")]

    assert fit_timeline(facts, at_most(5)) == [fact(2), fact(3, "speech")]


def test_long_timeline_shows_summaries_instead():
    facts = [fact(1, "summary"), fact(2), fact(3), fact(4, "summary"), fact(5), fact(6)]

    assert fit_timeline(facts, at_most(3)) == [fact(1, "summary"), fact(4, "summary")]


def test_a_very_long_timeline_keeps_the_days_that_fit():
    facts = [fact(index, "day") for index in range(5)] + [fact(9, "recording")] * 5

    assert fit_timeline(facts, at_most(2)) == [fact(0, "day"), fact(1, "day")]


def test_arguments_that_are_not_an_object_are_refused():
    assert read_arguments("not json") is None
    assert read_arguments("[1, 2]") is None
    assert read_arguments('{"query": "sign"}') == {"query": "sign"}


def test_an_unknown_tool_or_bad_arguments_give_an_error_not_a_crash():
    asking = Asking(conn=None)

    assert run_tool(asking, "teleport", {})[0].startswith("error")
    assert run_tool(asking, "search", None)[0].startswith("error")
    assert run_tool(asking, "timeline", {"start": None, "end": None})[0].startswith("error")
