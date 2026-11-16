"""Find the facts that best match a question.

    uv run python -m memory.search "what does the sign above the door say"

Two searches run over the facts and their results are combined:
  by meaning   the question is embedded and compared with every fact's embedding
  by keyword   the question's words are matched against each fact's text
A fact that ranks well in both ends up on top. Both can be limited to a stretch of time.
"""

import argparse
import sys
from datetime import datetime

from memory import db, llm

# How many results each of the two searches contributes before they are combined.
CANDIDATES = 20

# Standard constant for reciprocal rank fusion. Larger values flatten the gap between ranks.
FUSION_K = 60

# Keeps a search to a stretch of time. A start or end of null means no limit on that side.
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

# plainto_tsquery joins the words with AND; swapping in OR lets a fact match on any of them.
BY_KEYWORD = f"""
    with words as (select replace(plainto_tsquery('english', %(text)s)::text, '&', '|')::tsquery as query)
    select id from fact, words
     where text_search @@ words.query and {IN_RANGE}
     order by ts_rank(text_search, words.query) desc
     limit %(limit)s
"""


def fuse(rankings: list[list], k: int = FUSION_K) -> list[tuple]:
    """Combine several ranked lists into one, best first, as (item, score).

    Reciprocal rank fusion: an item scores 1 / (k + its position) in each list it appears in, and the scores add
    up. It only uses positions, so lists scored on different scales (distance, keyword rank) can be combined.
    """
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
    """Return the facts that best match a question, best first. start and end keep it to a stretch of time."""
    vector = llm.embed(conn, [question], "embed-question")[0]
    values = {"vector": vector, "text": question, "limit": CANDIDATES, "start": start, "end": end}

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


def clock(seconds: float) -> str:
    """Format seconds as m:ss."""
    return f"{int(seconds) // 60}:{int(seconds) % 60:02d}"


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

    for position, result in enumerate(results, start=1):
        matched = " + ".join(result["matched"])

        print(f"{position}. {result['kind']:<6} {clock(result['offset_seconds']):>5}  [{matched}]  {result['text']}")

        if result["image_path"]:
            print(f"   {result['image_path']}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
