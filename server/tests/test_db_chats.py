from datetime import datetime, timedelta, timezone

from server.db import turns_from

START = datetime(2026, 11, 18, 12, 0, tzinfo=timezone.utc)


def message(seconds, role, content, model_calls=0, usd=0.0):
    return {
        "id": f"{role}-{seconds}",
        "created_at": START + timedelta(seconds=seconds),
        "role": role,
        "content": content,
        "model_calls": model_calls,
        "usd": usd,
    }


def fact(fact_id):
    return {
        "id": fact_id,
        "kind": "text",
        "at": START.isoformat(),
        "moment_id": "m",
        "offset_seconds": 1.0,
        "text": f"fact {fact_id}",
        "image_path": None,
    }


def test_turn_gathers_sources_cost_and_time_from_its_messages():
    turns = turns_from(
        [
            message(0, "user", {"text": "What did the sign say?"}),
            message(1, "assistant", {"output": []}, model_calls=1, usd=0.01),
            message(2, "tool", {"name": "search", "facts": [fact(5), fact(7)]}, model_calls=1, usd=0.001),
            message(
                4,
                "assistant",
                {"output": [], "answer": {"answer": "Exit", "found": True, "sources": [7, 9, 7]}},
                model_calls=1,
                usd=0.02,
            ),
        ]
    )

    assert len(turns) == 1
    assert turns[0]["answer"] == "Exit" and turns[0]["found"] is True
    assert [source["id"] for source in turns[0]["sources"]] == [7]
    assert turns[0]["model_calls"] == 3 and abs(turns[0]["usd"] - 0.031) < 1e-9
    assert turns[0]["seconds"] == 4


def test_failed_question_shows_its_problem_and_the_next_question_starts_a_new_turn():
    turns = turns_from(
        [
            message(0, "user", {"text": "first"}),
            message(1, "assistant", {"error": "The answer failed: timeout"}),
            message(5, "user", {"text": "second"}),
            message(6, "assistant", {"answer": {"answer": "no", "found": False, "sources": []}, "facts": [fact(1)]}),
        ]
    )

    assert [turn["question"] for turn in turns] == ["first", "second"]
    assert turns[0]["answer"] is None and turns[0]["problem"] == "The answer failed: timeout"
    assert turns[1]["problem"] is None and turns[1]["sources"] == []
