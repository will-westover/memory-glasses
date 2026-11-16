from memory.evals import retrieved, summarise


def row(verdict, expected="Four.", supported=True, hit=True):
    return {"verdict": verdict, "expected": expected, "supported": supported, "retrieved": hit,
            "model_calls": 2, "seconds": 3.0, "usd": 0.001}


def test_retrieved_ignores_case_and_needs_one_phrase():
    shown = [{"text": "A: In this module we have Four Crew Quarters."}]

    assert retrieved(["four crew quarters", "something else"], shown)
    assert not retrieved(["telephone box"], shown)


def test_scores_split_answerable_from_unanswerable():
    rows = [row("correct"), row("partial"), row("wrong", expected=None, hit=None), row("correct", expected=None, hit=None)]

    totals = summarise(rows)

    assert totals["score"] == 0.625
    assert totals["answerable"] == 0.75
    assert totals["unanswerable"] == 0.5
    assert totals["retrieved"] == 1.0
