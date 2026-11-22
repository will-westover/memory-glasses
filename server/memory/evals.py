"""Ask every test question in both modes, have a judge model grade the answers, and report scores and cost."""

import argparse
import json
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from functools import partial
from typing import Literal

from pydantic import BaseModel, Field

from memory import db, llm, storage

from memory.agent import ask_agent, ask_plain
from memory.config import AGENT_MODEL, DATA_DIR, JUDGE_MODEL, SERVER_DIR

QUESTION_FILES = (SERVER_DIR / "evals" / "questions.json", DATA_DIR / "evals" / "questions.json")
RUNS_DIR = DATA_DIR / "evals" / "runs"

MODES = {"agent": ask_agent, "plain": ask_plain, "no-nearby": partial(ask_agent, use_nearby=False)}
DEFAULT_MODES = ("agent", "plain")
VERDICT_POINTS = {"correct": 1.0, "partial": 0.5, "wrong": 0.0}
SETS = ("dev", "test", "all")
DEFAULT_PARALLEL = 16
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

        questions += [{"kind": "other", "set": "dev", "moments": moments, **question} for question in listed]

    return questions


def mentions_evidence(evidence: list[str], shown: list[dict]) -> bool:
    """Return whether any fact the model was shown contains one of the evidence phrases."""
    texts = [fact["text"].lower() for fact in shown]

    return any(phrase.lower() in text for phrase in evidence for text in texts)


def found_photo(conn, photos: list[str], shown: list[dict]) -> bool:
    """Return whether any fact the model was shown is, or covers, one of the expected photos."""
    paths = {fact["image_path"] for fact in shown}
    covered = conn.execute(
        "select f.image_path from fact_frame ff join frame f on f.id = ff.frame_id where ff.fact_id = any(%s)",
        ([fact["id"] for fact in shown],),
    )

    return bool(set(photos) & (paths | {path for (path,) in covered}))


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
    photos = question.get("photos")

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
        "photo_found": found_photo(conn, photos, result["shown"]) if photos else None,
        "found_right": result["found"] == bool(question["expected"]),
        "model_calls": result["model_calls"],
        "seconds": result["seconds"],
        "usd": result["usd"],
    }


def average(values: list) -> float:
    """Return the mean of the values, or 0 if there are none."""
    return sum(values) / len(values) if values else 0.0


def percent(value: float | None) -> str:
    """Return a share as a whole percentage, or a dash when nothing was measured."""
    return "-" if value is None else f"{value:.0%}"


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
        "photo_found": average(photos)
        if (photos := [row["photo_found"] for row in rows if row.get("photo_found") is not None])
        else None,
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
        f" {'cited':>6} {'photo':>6} {'found ok':>9} {'calls':>6} {'seconds':>8} {'$ each':>9}"
    )

    for mode in MODES:
        of_mode = [row for row in rows if row["mode"] == mode]
        totals = mode_scores(of_mode)

        if not of_mode:
            continue

        print(
            f"{mode:<10} {totals['score']:>6.0%} {totals['answerable']:>11.0%} {totals['unanswerable']:>10.0%}"
            f" {totals['supported']:>10.0%} {totals['retrieved']:>10.0%} {totals['cited']:>6.0%} {percent(totals['photo_found']):>6}"
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


def answer_and_grade(question: dict, mode: str, model: str, run: int) -> dict:
    """Answer and grade one question on its own database connection, so many can run at once."""
    with db.connect() as conn:
        row = {**run_question(conn, question, mode, model), "run": run, "set": question["set"]}
        conn.commit()

    return row


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
    parser.add_argument("--set", choices=SETS, default="dev", help="dev while working; test only before a decision")
    parser.add_argument("--kind", action="append", help="ask only questions of these kinds")
    parser.add_argument("--parallel", type=int, default=DEFAULT_PARALLEL, help="how many questions to ask at once")
    args = parser.parse_args()

    llm.require_price(JUDGE_MODEL)
    questions = [
        question
        for question in load_questions()
        if (args.set == "all" or question["set"] == args.set) and (not args.kind or question["kind"] in args.kind)
    ][: args.limit]
    modes = args.mode or list(DEFAULT_MODES)
    jobs = [(question, mode, run) for run in range(args.repeat) for question in questions for mode in modes]

    with db.connect() as conn:
        judge_before = db.step_cost(conn, "judge")

    rows = []

    with ThreadPoolExecutor(max(1, args.parallel)) as pool:
        futures = [pool.submit(answer_and_grade, question, mode, args.model, run) for question, mode, run in jobs]

        for done, future in enumerate(as_completed(futures), start=1):
            rows.append(future.result())
            print(f"  {done}/{len(jobs)} {rows[-1]['question']}", file=sys.stderr)

    position = {question["question"]: number for number, question in enumerate(questions)}
    rows.sort(key=lambda row: (row["run"], position[row["question"]], modes.index(row["mode"])))

    with db.connect() as conn:
        judge_usd = db.step_cost(conn, "judge") - judge_before

    print_report(rows, judge_usd)
    save_run(rows, args.model, judge_usd)

    return 0


if __name__ == "__main__":
    sys.exit(main())
