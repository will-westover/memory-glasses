"""Ask every test question in both modes, have a judge model grade the answers, and report scores and cost."""

import argparse
import json
import sys
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

from functools import partial

from memory import db, llm, storage

from memory.agent import ask_agent, ask_plain
from memory.config import AGENT_MODEL, DATA_DIR, JUDGE_MODEL, SERVER_DIR

QUESTION_FILES = (SERVER_DIR / "evals" / "questions.json", DATA_DIR / "evals" / "questions.json")
RUNS_DIR = DATA_DIR / "evals" / "runs"

MODES = {"agent": ask_agent, "plain": ask_plain, "no-nearby": partial(ask_agent, use_nearby=False)}
DEFAULT_MODES = ("agent", "plain")
VERDICT_POINTS = {"correct": 1.0, "partial": 0.5, "wrong": 0.0}
NOT_RECORDED = "NOT RECORDED"

JUDGE_INSTRUCTIONS = f"""\
You grade one answer from an assistant that answers questions about what a person's camera glasses recorded.
You are given the question, a reference answer, the assistant's answer, and the facts the assistant cited.

verdict:
- correct: the answer states the key facts of the reference and contradicts none of them. Extra true detail and
  different wording are fine.
- partial: the answer gets some of the reference right and misses or blurs the rest.
- wrong: the answer contradicts the reference, misses it entirely, or says it could not be found.
When the reference is {NOT_RECORDED}, nothing in the record answers the question. Then the answer is correct
only if it says the record does not show this, and wrong if it gives an answer.

supported: true if every claim in the answer is backed by the cited facts. An answer that only says the record
does not show something is supported. An answer that makes claims with no cited facts is not."""


class Grade(BaseModel):
    reason: str = Field(description="One sentence explaining the verdict")
    verdict: Literal["correct", "partial", "wrong"]
    supported: bool = Field(description="True if every claim in the answer is backed by the cited facts")


def load_questions() -> list[dict]:
    """Return the test questions from every question file that exists, each with the moments its file is about."""
    questions = []

    for path in QUESTION_FILES:
        if storage.local_copy(path) is None:
            continue

        loaded = json.loads(path.read_text())
        listed = loaded if isinstance(loaded, list) else loaded["questions"]
        moments = None if isinstance(loaded, list) else loaded.get("moments")

        questions += [{"kind": "other", "moments": moments, **question} for question in listed]

    return questions


def mentions_evidence(evidence: list[str], shown: list[dict]) -> bool:
    """Return whether any fact the model was shown contains one of the evidence phrases."""
    texts = [fact["text"].lower() for fact in shown]

    return any(phrase.lower() in text for phrase in evidence for text in texts)


def grade(conn, question: dict, result: dict) -> Grade:
    """Ask the judge model to grade one answer."""
    cited = "\n".join(f"- {fact['text']}" for fact in result["sources"]) or "(none)"
    answer = result["answer"] if result["found"] else f"(could not be found) {result['answer']}"

    response = llm.client().responses.parse(
        model=JUDGE_MODEL,
        instructions=JUDGE_INSTRUCTIONS,
        input=f"Question: {question['question']}\nReference: {question['expected'] or NOT_RECORDED}\n"
        f"Answer: {answer}\nCited facts:\n{cited}",
        text_format=Grade,
        reasoning={"effort": "low"},
    )
    llm.log_cost(conn, "judge", JUDGE_MODEL, response.usage)

    return response.output_parsed


def run_question(conn, question: dict, mode: str, model: str) -> dict:
    """Answer one question in one mode and return the graded result."""
    result = MODES[mode](conn, question["question"], model, moments=question["moments"])
    graded = grade(conn, question, result)
    evidence = question.get("evidence")

    return {
        "question": question["question"],
        "kind": question["kind"],
        "expected": question["expected"],
        "mode": mode,
        "answer": result["answer"],
        "found": result["found"],
        "verdict": graded.verdict,
        "supported": graded.supported,
        "reason": graded.reason,
        "retrieved": mentions_evidence(evidence, result["shown"]) if evidence else None,
        "cited": mentions_evidence(evidence, result["sources"]) if evidence else None,
        "found_right": result["found"] == bool(question["expected"]),
        "model_calls": result["model_calls"],
        "seconds": result["seconds"],
        "usd": result["usd"],
    }


def average(values: list) -> float:
    """Return the mean of the values, or 0 if there are none."""
    return sum(values) / len(values) if values else 0.0


def mode_scores(rows: list[dict]) -> dict:
    """Return the scores and averages for one mode's rows."""
    answerable = [row for row in rows if row["expected"]]
    unanswerable = [row for row in rows if not row["expected"]]

    return {
        "questions": len(rows),
        "score": average([VERDICT_POINTS[row["verdict"]] for row in rows]),
        "answerable": average([VERDICT_POINTS[row["verdict"]] for row in answerable]),
        "unanswerable": average([VERDICT_POINTS[row["verdict"]] for row in unanswerable]),
        "supported": average([row["supported"] for row in rows]),
        "retrieved": average([row["retrieved"] for row in rows if row["retrieved"] is not None]),
        "cited": average([row["cited"] for row in rows if row["cited"] is not None]),
        "found_right": average([row["found_right"] for row in rows]),
        "model_calls": average([row["model_calls"] for row in rows]),
        "seconds": average([row["seconds"] for row in rows]),
        "usd": average([row["usd"] for row in rows]),
    }


def by_kind(rows: list[dict]) -> dict:
    """Return each kind of question's count and score."""
    kinds = dict.fromkeys(row["kind"] for row in rows)

    return {
        kind: {"questions": len(of_kind), "score": average([VERDICT_POINTS[row["verdict"]] for row in of_kind])}
        for kind in kinds
        for of_kind in [[row for row in rows if row["kind"] == kind]]
    }


def spread(rows: list[dict]) -> tuple[float, float]:
    """Return the lowest and highest score among the repeats of a run."""
    scores = [
        average([VERDICT_POINTS[row["verdict"]] for row in rows if row["run"] == run])
        for run in dict.fromkeys(row["run"] for row in rows)
    ]

    return min(scores), max(scores)


def print_report(rows: list[dict], judge_usd: float) -> None:
    for row in rows:
        if row["verdict"] != "correct" or not row["supported"]:
            flag = row["verdict"] + ("" if row["supported"] else ", unsupported")
            print(f"  [{row['mode']}] {flag}: {row['question']}\n      {row['answer']}\n      judge: {row['reason']}")

    print(
        f"\n{'mode':<10} {'score':>6} {'answerable':>11} {'no answer':>10} {'supported':>10} {'retrieved':>10}"
        f" {'cited':>6} {'found ok':>9} {'calls':>6} {'seconds':>8} {'$ each':>9}"
    )

    for mode in MODES:
        of_mode = [row for row in rows if row["mode"] == mode]
        totals = mode_scores(of_mode)

        if not of_mode:
            continue

        print(
            f"{mode:<10} {totals['score']:>6.0%} {totals['answerable']:>11.0%} {totals['unanswerable']:>10.0%}"
            f" {totals['supported']:>10.0%} {totals['retrieved']:>10.0%} {totals['cited']:>6.0%}"
            f" {totals['found_right']:>9.0%} {totals['model_calls']:>6.1f} {totals['seconds']:>8.1f}"
            f" {totals['usd']:>9.5f}"
        )

        low, high = spread(of_mode)

        if low != high:
            print(f"{'':<10} score ranged from {low:.0%} to {high:.0%} across repeats")

        for kind, entry in by_kind(of_mode).items():
            print(f"{'':<10} {kind:<16} {entry['score']:>5.0%}  ({entry['questions']})")

    answers_usd = sum(row["usd"] for row in rows)
    print(
        f"\nThis run cost ${answers_usd + judge_usd:.4f}: ${answers_usd:.4f} for answers, ${judge_usd:.4f} for the judge."
    )


def save_run(rows: list[dict], model: str, judge_usd: float) -> None:
    """Write the run's rows and scores to a dated file in data/evals/runs, and keep a copy in storage."""
    RUNS_DIR.mkdir(parents=True, exist_ok=True)
    path = RUNS_DIR / f"{datetime.now():%Y-%m-%dT%H-%M-%S}.json"

    ran = dict.fromkeys(row["mode"] for row in rows)
    scores = {mode: mode_scores([row for row in rows if row["mode"] == mode]) for mode in ran}
    kinds = {mode: by_kind([row for row in rows if row["mode"] == mode]) for mode in ran}
    path.write_text(
        json.dumps(
            {
                "model": model,
                "judge": JUDGE_MODEL,
                "judge_usd": judge_usd,
                "scores": scores,
                "kinds": kinds,
                "rows": rows,
            },
            indent=2,
        )
    )
    storage.save(path)

    print(f"Saved to {path}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Grade the agent and the plain baseline on the test questions.")
    parser.add_argument("--limit", type=int, help="ask only the first few questions")
    parser.add_argument(
        "--mode",
        choices=list(MODES),
        action="append",
        help="modes to run (default: agent and plain); no-nearby is the agent without the nearby tool",
    )
    parser.add_argument("--model", default=AGENT_MODEL, help="the model that answers")
    parser.add_argument("--repeat", type=int, default=1, help="ask every question this many times, to see the spread")
    args = parser.parse_args()

    llm.require_price(JUDGE_MODEL)
    questions = load_questions()[: args.limit]
    modes = args.mode or list(DEFAULT_MODES)
    rows = []

    with db.connect() as conn:
        judge_before = db.step_cost(conn, "judge")

        for run in range(args.repeat):
            for number, question in enumerate(questions, start=1):
                for mode in modes:
                    rows.append({**run_question(conn, question, mode, args.model), "run": run})
                    conn.commit()

                print(f"  {number}/{len(questions)} {question['question']}", file=sys.stderr)

        judge_usd = db.step_cost(conn, "judge") - judge_before

    print_report(rows, judge_usd)
    save_run(rows, args.model, judge_usd)

    return 0


if __name__ == "__main__":
    sys.exit(main())
