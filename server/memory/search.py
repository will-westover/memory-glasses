"""Find the facts that best match a question, by meaning and by keyword."""

import argparse
import sys
from datetime import datetime

from memory import db, llm
from memory.config import clock

CANDIDATES = 20

FUSION_K = 60

IN_RANGE = """
    (%(start)s::timestamptz is null or at >= %(start)s::timestamptz)
    and (%(end)s::timestamptz is null or at <= %(end)s::timestamptz)
"""

BY_MEANING = f"""
    select id from fact
     where embedding is not null and {IN_RANGE}
     order by embedding <=> %(vector)s::vector
     limit %(limit)s
"""

BY_KEYWORD = f"""
    with words as (select replace(plainto_tsquery('english', %(text)s)::text, '&', '|')::tsquery as query)
    select id from fact, words
     where text_search @@ words.query and {IN_RANGE}
     order by ts_rank(text_search, words.query) desc
     limit %(limit)s
"""


def fuse(rankings: list[list], k: int = FUSION_K) -> list[tuple]:
    """Combine ranked lists into one by reciprocal rank fusion, best first, as (item, score)."""
    scores: dict = {}

    for ranking in rankings:
        for position, item in enumerate(ranking, start=1):
            scores[item] = scores.get(item, 0.0) + 1.0 / (k + position)

    return sorted(scores.items(), key=lambda pair: pair[1], reverse=True)


def search(
    conn,
    question: str,
    limit: int = 5,
    start: datetime | None = None,
    end: datetime | None = None,
) -> list[dict]:
    """Return the facts that best match a question, best first."""
    vector = llm.embed(conn, [question], "embed-question")[0]
    values = {"vector": vector, "text": question, "limit": CANDIDATES, "start": start, "end": end}

    if start or end:
        conn.execute("set local hnsw.iterative_scan = relaxed_order")

    by_meaning = [fact_id for (fact_id,) in conn.execute(BY_MEANING, values)]
    by_keyword = [fact_id for (fact_id,) in conn.execute(BY_KEYWORD, values)]

    ranked = fuse([by_meaning, by_keyword])[:limit]
    details = {fact["id"]: fact for fact in db.facts_by_id(conn, [fact_id for fact_id, _ in ranked])}

    return [
        {
            **details[fact_id],
            "score": score,
            "matched": [name for name, hits in (("meaning", by_meaning), ("keyword", by_keyword)) if fact_id in hits],
        }
        for fact_id, score in ranked
    ]


def print_results(results: list[dict]) -> None:
    for position, result in enumerate(results, start=1):
        matched = " + ".join(result["matched"])

        print(f"{position}. {result['kind']:<6} {clock(result['offset_seconds']):>5}  [{matched}]  {result['text']}")

        if result["image_path"]:
            print(f"   {result['image_path']}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Search the facts.")
    parser.add_argument("question")
    parser.add_argument("--limit", type=int, default=5)
    args = parser.parse_args()

    with db.connect() as conn:
        results = search(conn, args.question, args.limit)

    if not results:
        print("Nothing to search yet. Run the captions, transcript and facts steps first.")
        return 0

    print_results(results)

    return 0


if __name__ == "__main__":
    sys.exit(main())
