from server.evals import by_kind, mentions_evidence, mode_scores


def row(verdict, expected="Four.", supported=True, hit=True):
    return {
        "verdict": verdict,
        "expected": expected,
        "supported": supported,
        "retrieved": hit,
        "cited": hit,
        "found_right": True,
        "kind": "said",
        "model_calls": 2,
        "seconds": 3.0,
        "usd": 0.001,
    }


def test_retrieved_ignores_case_and_needs_one_phrase():
    shown = [{"text": "A: In this module we have Four Crew Quarters."}]

    assert mentions_evidence(["four crew quarters", "something else"], shown)
    assert not mentions_evidence(["telephone box"], shown)


def test_scores_split_answerable_from_unanswerable():
    rows = [
        row("correct"),
        row("partial"),
        row("wrong", expected=None, hit=None),
        row("correct", expected=None, hit=None),
    ]

    totals = mode_scores(rows)

    assert totals["score"] == 0.625
    assert totals["answerable"] == 0.75
    assert totals["unanswerable"] == 0.5
    assert totals["retrieved"] == 1.0


def test_scores_are_grouped_by_kind():
    rows = [row("correct"), row("wrong"), {**row("correct"), "kind": "seen"}]

    assert by_kind(rows) == {"said": {"questions": 2, "score": 0.5}, "seen": {"questions": 1, "score": 1.0}}
